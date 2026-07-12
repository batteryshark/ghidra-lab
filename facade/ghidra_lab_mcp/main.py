from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastmcp import FastMCP
from fastmcp.server import create_proxy
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools.tool_transform import ToolTransformConfig

from .ghidra_rest import GhidraRestClient, GhidraRestError
from .sample_store import SampleStore, StoreError, utc_now
from .server_import import clean_name, import_to_repository, normalize_folder
from .settings import settings


# Background import jobs (analyzeHeadless of a real binary takes minutes — far
# longer than an MCP request may stay open). Keyed by sample_id; survives across
# MCP sessions because the facade process is long-lived.
_import_jobs: dict[str, dict[str, Any]] = {}
_import_lock = threading.Lock()


# Tools kept in the small default `list_tools` view. Everything else (the full
# upstream Ghidra catalog) is reachable through search_tools/call_tool.
ALWAYS_VISIBLE_TOOLS = [
    "lab_sample_upload_start",
    "lab_sample_list",
    "lab_sample_get",
    "lab_server_status",
    "lab_repository_list",
    "lab_repository_import_sample",
    "lab_repository_import_status",
    "lab_repository_load_program",
    "lab_repository_checkin",
    "lab_repository_undo_checkout",
    "list_instances",
    "connect_instance",
    "check_tools",
]


store = SampleStore(
    root=settings.sample_root,
    public_base_url=settings.public_base_url,
    max_upload_bytes=settings.max_upload_bytes,
)
ghidra = GhidraRestClient(settings.ghidra_rest_url, timeout=settings.request_timeout)
mcp = FastMCP("Ghidra Lab")


# --------------------------------------------------------------------- sessions

def _ensure_shared_session() -> dict[str, Any]:
    """Guarantee the headless server has the server-bound shared project open.

    Self-healing: tolerates JVM restarts and stray /close_project. Raises a
    RuntimeError carrying the upstream diagnostics if the project cannot be
    opened or is not server-bound (usually means the one-time bootstrap has not
    completed — see /data/logs/bootstrap.log).
    """
    try:
        info = ghidra.get_project_info()
    except GhidraRestError:
        info = {}

    if not (info.get("has_project") and info.get("project_server_bound")):
        try:
            ghidra.open_project(str(settings.shared_project_gpr))
            ghidra.server_connect(settings.ghidra_server_host, settings.ghidra_server_port)
            info = ghidra.get_project_info()
        except GhidraRestError as exc:
            raise RuntimeError(
                f"Shared project unavailable ({exc}). Expected {settings.shared_project_gpr}; "
                "confirm the Ghidra Server is reachable and the bootstrap ran "
                "(/data/logs/bootstrap.log). Diagnostics: " + str(exc.payload)
            ) from exc

    if not info.get("project_server_bound"):
        raise RuntimeError(
            "The open project is not bound to a Ghidra Server, so version control is "
            f"unavailable. project_info={info}"
        )
    return info


def _ensure_server_connected() -> None:
    """Best-effort: ensure a live server connection (no project required)."""
    try:
        ghidra.server_connect(settings.ghidra_server_host, settings.ghidra_server_port)
    except GhidraRestError:
        pass


# ----------------------------------------------------------------- sample tools

@mcp.tool
def lab_sample_upload_start(
    filename: str,
    project: str = "default",
    collection: str = "default",
    size: int | None = None,
    sha256: str | None = None,
) -> dict[str, Any]:
    """Create a one-shot upload URL for a binary sample. PUT the raw bytes to the
    returned upload_url (the URL carries its own token — no auth header needed),
    then call lab_repository_import_sample with the sample_id."""
    record = store.create_upload(
        filename=filename,
        project=project,
        collection=collection,
        size=size,
        sha256=sha256,
    )
    token = store.peek_token(record["sample_id"])
    put_url = f"{record['upload_url']}?token={token}"
    record["upload_url"] = put_url
    record["upload_instructions"] = (
        "PUT the raw file bytes to upload_url. No Authorization header is needed — "
        "the URL carries a one-time token. Then call "
        "lab_repository_import_sample(sample_id) to import + analyze it."
    )
    record["curl_example"] = f'curl -X PUT --data-binary @{filename} "{put_url}"'
    return record


@mcp.tool
def lab_sample_list(
    project: str | None = None,
    collection: str | None = None,
    include_pending: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """List uploaded binary samples available to import into Ghidra."""
    return store.list_samples(
        project=project,
        collection=collection,
        include_pending=include_pending,
        limit=limit,
    )


@mcp.tool
def lab_sample_get(sample_id: str) -> dict[str, Any]:
    """Get metadata and a download URL for one uploaded binary sample."""
    return store.get_sample(sample_id)


@mcp.tool
def lab_sample_download_url(sample_id: str) -> dict[str, Any]:
    """Return a download URL for a completed binary sample uploaded to the lab."""
    record = store.get_sample(sample_id)
    if record.get("status") != "complete":
        raise ValueError("sample upload is not complete")
    token = store.peek_token(sample_id)
    return {
        "sample_id": sample_id,
        "method": "GET",
        "url": f"{record['download_url']}?token={token}",
        "auth": "token is in the URL; no Authorization header needed",
    }


@mcp.tool
def lab_sample_delete(sample_id: str, delete_file: bool = False) -> dict[str, Any]:
    """Delete metadata for an uploaded binary sample and optionally remove its file."""
    return store.delete_sample(sample_id, delete_file=delete_file)


@mcp.tool
def lab_sample_import(
    sample_id: str,
    language: str | None = None,
    compiler_spec: str | None = None,
) -> dict[str, Any]:
    """Quick look: load an uploaded sample into the headless Ghidra instance
    in-memory, WITHOUT committing it to the Ghidra Server. For persistent,
    GUI-visible work use lab_repository_import_sample instead."""
    record = store.get_sample(sample_id)
    if record.get("status") != "complete":
        raise ValueError("sample upload is not complete")
    try:
        result = ghidra.load_program(
            Path(record["container_path"]),
            language=language,
            compiler_spec=compiler_spec,
        )
    except GhidraRestError as exc:
        raise RuntimeError(str(exc)) from exc
    updated = store.append_import(sample_id, {"mode": "in_memory", "ghidra": result})
    return {"sample": updated, "ghidra": result}


# ------------------------------------------------------------- repository tools

@mcp.tool
def lab_server_status() -> dict[str, Any]:
    """Show the lab's Ghidra Server connection and open shared-project state."""
    status: dict[str, Any] = {}
    try:
        status["server"] = ghidra.server_status()
    except GhidraRestError as exc:
        status["server_error"] = str(exc)
    try:
        status["project"] = ghidra.get_project_info()
    except GhidraRestError as exc:
        status["project_error"] = str(exc)
    status["configured_server"] = {
        "host": settings.ghidra_server_host,
        "port": settings.ghidra_server_port,
        "user": settings.ghidra_server_user,
        "default_repository": settings.default_repository,
        "shared_project": str(settings.shared_project_gpr),
    }
    return status


@mcp.tool
def lab_repository_list(repository: str | None = None, path: str = "/") -> dict[str, Any]:
    """List files in a Ghidra Server repository folder."""
    repo = _repository_name(repository)
    _ensure_server_connected()
    try:
        files = ghidra.list_repository_files(repo, path)
    except GhidraRestError as exc:
        raise RuntimeError(str(exc)) from exc
    return {"repository": repo, "path": path, "files": files}


@mcp.tool
def lab_repository_import_sample(
    sample_id: str,
    repository: str | None = None,
    folder: str | None = None,
    language: str | None = None,
    compiler_spec: str | None = None,
    analyze: bool = True,
    overwrite: bool = False,
    load_after_import: bool = True,
    checkout_for_edit: bool = False,
    commit_comment: str | None = None,
) -> dict[str, Any]:
    """Import an uploaded sample into the Ghidra Server repository: it is imported,
    analyzed, and committed as version 1 — immediately visible in the GUI.

    By default it then loads the program READ-ONLY and leaves it UNLOCKED, so a
    human can open/edit it in the GUI and the agent can analyze it. Pass
    checkout_for_edit=True only when the agent will edit and check the program
    back in, which takes an exclusive lock until lab_repository_checkin.

    analyzeHeadless analysis of a real binary takes minutes, so this runs in the
    BACKGROUND: it returns immediately with status 'running' and the program_path.
    Poll lab_repository_import_status(sample_id) until status is 'complete' (or
    'failed') before working on the program."""
    record = store.get_sample(sample_id)
    if record.get("status") != "complete":
        raise ValueError("sample upload is not complete")

    repo = _repository_name(repository, record)
    target_folder = _folder_name(folder, record)
    program_path = _program_path(target_folder, record["filename"])
    comment = commit_comment or f"Import {record['filename']} via Ghidra Lab"

    with _import_lock:
        existing = _import_jobs.get(sample_id)
        if existing and existing.get("status") == "running":
            return {
                "sample_id": sample_id,
                "status": "running",
                "program_path": existing.get("program_path", program_path),
                "message": "An import for this sample is already running. Poll lab_repository_import_status(sample_id).",
            }
        _import_jobs[sample_id] = {
            "status": "running",
            "program_path": program_path,
            "repository": repo,
            "folder": target_folder,
            "started_at": utc_now(),
        }

    threading.Thread(
        target=_run_import_job,
        args=(sample_id, record, repo, target_folder, program_path, comment,
              language, compiler_spec, analyze, overwrite, load_after_import,
              checkout_for_edit),
        daemon=True,
    ).start()

    return {
        "sample_id": sample_id,
        "status": "running",
        "repository": repo,
        "program_path": program_path,
        "message": (
            "Import started in the background (analyzeHeadless import + analysis "
            "can take minutes for a real binary). Poll "
            "lab_repository_import_status(sample_id) until status is 'complete'."
        ),
    }


def _run_import_job(
    sample_id: str,
    record: dict[str, Any],
    repo: str,
    target_folder: str,
    program_path: str,
    comment: str,
    language: str | None,
    compiler_spec: str | None,
    analyze: bool,
    overwrite: bool,
    load_after_import: bool,
    checkout_for_edit: bool,
) -> None:
    try:
        server_import = import_to_repository(
            file_path=record["container_path"],
            repository=repo,
            folder=target_folder,
            language=language,
            compiler_spec=compiler_spec,
            analyze=analyze,
            overwrite=overwrite,
            commit_comment=comment,
        )

        load_result: dict[str, Any] | None = None
        if load_after_import and repo == settings.default_repository:
            try:
                _ensure_shared_session()
                if checkout_for_edit:
                    checkout = ghidra.lab_checkout(program_path)
                    loaded = ghidra.load_program_from_project(program_path)
                    load_result = {"mode": "writable", "checkout": checkout, "loaded": loaded}
                else:
                    loaded = ghidra.load_program_from_project(program_path)
                    load_result = {
                        "mode": "read_only",
                        "loaded": loaded,
                        "note": (
                            "Committed as version 1 and loaded read-only, left UNLOCKED so it "
                            "opens in the GUI. To edit + persist, call "
                            "lab_repository_load_program (writable) then lab_repository_checkin."
                        ),
                    }
            except (GhidraRestError, RuntimeError) as exc:
                load_result = {"error": str(exc), "program_path": program_path}
        elif load_after_import:
            load_result = {
                "skipped": (
                    f"imported to '{repo}'; only the bound repository "
                    f"'{settings.default_repository}' is loadable for interactive MCP work"
                )
            }

        import_record = {
            "mode": "ghidra_server_repository",
            "repository": repo,
            "folder": target_folder,
            "program_path": program_path,
            "server_import": server_import,
            "load_after_import": load_result,
        }
        store.append_import(sample_id, import_record)
        with _import_lock:
            _import_jobs[sample_id] = {
                "status": "complete",
                "program_path": program_path,
                "repository": repo,
                "result": import_record,
                "finished_at": utc_now(),
            }
    except Exception as exc:  # noqa: BLE001 — surface any failure to the poller
        with _import_lock:
            _import_jobs[sample_id] = {
                "status": "failed",
                "program_path": program_path,
                "repository": repo,
                "error": str(exc),
                "finished_at": utc_now(),
            }


@mcp.tool
def lab_repository_import_status(sample_id: str) -> dict[str, Any]:
    """Check a lab_repository_import_sample job. status is 'running', 'complete'
    (with the import result and load state), or 'failed' (with the error)."""
    with _import_lock:
        job = dict(_import_jobs.get(sample_id, {}))
    if job:
        return {"sample_id": sample_id, **job}
    # No in-memory job (e.g. the facade restarted) — fall back to the manifest.
    try:
        record = store.get_sample(sample_id)
    except StoreError as exc:
        raise RuntimeError(str(exc)) from exc
    imports = record.get("imports") or []
    if imports:
        return {"sample_id": sample_id, "status": "complete", "result": imports[-1].get("result")}
    return {
        "sample_id": sample_id,
        "status": "unknown",
        "message": "No import job found for this sample; call lab_repository_import_sample first.",
    }


@mcp.tool
def lab_repository_load_program(
    path: str,
    repository: str | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    """Open a repository program for analysis, e.g. path='/Malware/sample.exe'.

    By default it checks the program out (so your edits can be committed with
    lab_repository_checkin) and loads it writable. If the program is already
    checked out by someone else (e.g. the human working in the Ghidra GUI), or you
    pass read_only=True, it loads READ-ONLY instead: you can still decompile,
    list functions, follow xrefs, etc., but cannot check in changes until the
    checkout is released. Pass read_only=True when you only want to look at a
    program without taking the edit lock from whoever else may want it."""
    if not path.strip():
        raise ValueError("path is required, for example /Malware/sample.exe")
    repo = _repository_name(repository)
    _ensure_shared_session()

    mode = "read_only"
    checkout: dict[str, Any] | None = None
    if not read_only:
        try:
            checkout = ghidra.lab_checkout(path)
            mode = "writable"
        except GhidraRestError as exc:
            # Could not check out (typically held by another user) — degrade to a
            # read-only load so analysis still works.
            checkout = {"checked_out": False, "checked_out_by": _checkout_holder(repo, path)}
            mode = "read_only"

    try:
        loaded = ghidra.load_program_from_project(path)
    except GhidraRestError as exc:
        raise RuntimeError(str(exc)) from exc

    result: dict[str, Any] = {
        "repository": repo,
        "path": path,
        "mode": mode,
        "checkout": checkout,
        "loaded": loaded,
    }
    if mode == "writable":
        result["note"] = "Writable — edit, then lab_repository_checkin to save a new version."
    else:
        holder = (checkout or {}).get("checked_out_by")
        result["note"] = (
            "READ-ONLY: analysis works (decompile, list functions, xrefs) but "
            "check-in is unavailable."
            + (f" Currently checked out by '{holder}'." if holder else "")
            + " To edit, retry once it is released (GUI: Undo Checkout / Check In)."
        )
    return result


@mcp.tool
def lab_repository_undo_checkout(path: str, keep_local_copy: bool = False) -> dict[str, Any]:
    """Release a checkout WITHOUT committing — discards uncommitted changes to
    this program and frees it for the human GUI user (or another session) to edit."""
    if not path.strip():
        raise ValueError("path is required")
    _ensure_shared_session()
    try:
        return ghidra.lab_undo_checkout(path, keep=keep_local_copy)
    except GhidraRestError as exc:
        raise RuntimeError(str(exc)) from exc


@mcp.tool
def lab_repository_checkin(
    path: str,
    comment: str = "Agent analysis update",
    keep_checked_out: bool = False,
    save_before_checkin: bool = True,
    repository: str | None = None,
) -> dict[str, Any]:
    """Save and check in a repository program after MCP analysis, creating a new
    version the human GUI user can open. `path` is the repository program path
    returned by import/load (e.g. '/AgentSmoke/sample.exe')."""
    if not path.strip():
        raise ValueError("path is required")
    repo = _repository_name(repository)
    _ensure_shared_session()
    try:
        checkin = ghidra.lab_checkin(
            path,
            comment=comment,
            keep_checked_out=keep_checked_out,
            save_before_checkin=save_before_checkin,
        )
    except GhidraRestError as exc:
        raise RuntimeError(str(exc)) from exc
    return {"repository": repo, "path": path, "checkin": checkin}


# ------------------------------------------------------------- naming helpers

def _repository_name(repository: str | None, record: dict[str, Any] | None = None) -> str:
    if repository and repository.strip():
        return clean_name(repository, settings.default_repository)
    if record:
        project = record.get("project")
        if project and project != "default":
            return clean_name(project, settings.default_repository)
    return clean_name(settings.default_repository, "GhidraLab")


def _folder_name(folder: str | None, record: dict[str, Any]) -> str:
    if folder and folder.strip():
        return normalize_folder(folder)
    collection = record.get("collection")
    if collection and collection != "default":
        return normalize_folder(collection)
    return "/"


def _program_path(folder: str, filename: str) -> str:
    clean_folder = normalize_folder(folder)
    clean_filename = Path(filename).name
    if clean_folder == "/":
        return f"/{clean_filename}"
    return f"{clean_folder}/{clean_filename}"


def _checkout_holder(repo: str, path: str) -> str | None:
    """Best-effort: who currently holds the checkout on a repository program."""
    try:
        info = ghidra.server_checkouts(repo, path)
    except GhidraRestError:
        return None
    checkouts = info.get("checkouts") or []
    return checkouts[0].get("user") if checkouts else None


# ------------------------------------------------------- mount + search + app

mcp.mount(create_proxy(settings.ghidra_bridge_mcp_url))

# The bridge auto-exposes the headless server's low-level project/version-control
# endpoints as MCP tools. They are agent footguns — either internal plumbing the
# facade calls directly over HTTP, broken in headless mode, or destructive — and
# every one is superseded by an orchestrated lab_* tool that opens the shared
# project and checks out/loads/commits correctly. Hide them from list_tools and
# search so agents reach for the right tool. (The facade still calls the
# underlying REST endpoints directly; this only affects the MCP surface.)
HIDDEN_BRIDGE_TOOLS = [
    # internal version-control plumbing -> use lab_repository_load_program / _checkin
    "lab_checkout",
    "lab_checkin",
    "lab_undo_checkout",
    # raw program/project ops that strand or break an agent
    "load_program_from_project",  # read-only copy without checkout (the "can't save" trap)
    "save_program",               # fails "Location does not exist" on checked-out files
    "import_file",                # GUI-only; errors headless
    "open_project",               # stomps the facade-managed shared session
    "close_project",
    "create_project",             # makes a stray local project
    "delete_file",                # destructive
]
for _name in HIDDEN_BRIDGE_TOOLS:
    mcp.add_tool_transformation(_name, ToolTransformConfig(enabled=False))

mcp.add_transform(
    BM25SearchTransform(
        max_results=settings.search_results,
        always_visible=ALWAYS_VISIBLE_TOOLS,
    )
)


mcp_app = mcp.http_app(path="/mcp")
app = FastAPI(
    title="Ghidra Lab",
    lifespan=mcp_app.lifespan,
    routes=list(mcp_app.routes),
)


@app.middleware("http")
async def bearer_auth(request: Request, call_next):
    path = request.url.path
    # /uploads and /downloads carry their own per-sample bearer token, validated
    # in the handlers, so an agent can transfer files without holding the MCP
    # bearer token (which only its harness has).
    if (
        settings.insecure
        or path == "/healthz"
        or path.startswith("/uploads/")
        or path.startswith("/downloads/")
    ):
        return await call_next(request)
    expected = f"Bearer {settings.token}"
    if request.headers.get("authorization") != expected:
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "service": "ghidra-lab"}


@app.put("/uploads/{sample_id}")
async def upload_sample(sample_id: str, request: Request) -> dict[str, Any]:
    if not store.verify_token(sample_id, request.query_params.get("token")):
        raise HTTPException(status_code=401, detail="invalid or missing upload token")
    try:
        return await store.save_upload(sample_id, request.stream())
    except StoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@app.get("/downloads/{sample_id}")
def download_sample(sample_id: str, request: Request) -> FileResponse:
    if not store.verify_token(sample_id, request.query_params.get("token")):
        raise HTTPException(status_code=401, detail="invalid or missing download token")
    try:
        path, manifest = store.get_download_path(sample_id)
    except StoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return FileResponse(
        path=path,
        media_type="application/octet-stream",
        filename=manifest.get("filename", path.name),
    )


@app.get("/samples")
def list_samples(
    project: str | None = None,
    collection: str | None = None,
    include_pending: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    try:
        return store.list_samples(
            project=project,
            collection=collection,
            include_pending=include_pending,
            limit=limit,
        )
    except StoreError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def main() -> None:
    uvicorn.run(
        "ghidra_lab_mcp.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
