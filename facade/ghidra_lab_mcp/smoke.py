"""In-container smoke test for the Ghidra Lab MCP facade.

Runs inside the ghidra-lab container against the local facade, exercising the
full agent flow: tool listing, search, sample upload, server-repository import,
an upstream tool call, and a check-in round-trip. Exits non-zero on any failure.

    docker compose exec ghidra-lab python -m ghidra_lab_mcp.smoke
"""

from __future__ import annotations

import asyncio
import re
import sys
import urllib.request

from fastmcp import Client

from .settings import settings

BASE = f"http://127.0.0.1:{settings.port}"
MCP_URL = f"{BASE}/mcp"
AUTH = {"Authorization": f"Bearer {settings.token}"}

PINNED = {
    "lab_sample_upload_start",
    "lab_sample_list",
    "lab_sample_get",
    "lab_server_status",
    "lab_repository_list",
    "lab_repository_import_sample",
    "lab_repository_load_program",
    "lab_repository_checkin",
    "list_instances",
    "connect_instance",
    "check_tools",
    "search_tools",
    "call_tool",
}

SAMPLE_BINARY = "/usr/bin/true"


def _say(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


def _result_data(result):
    data = getattr(result, "data", None)
    if data is not None:
        return data
    blocks = getattr(result, "content", None) or []
    for block in blocks:
        text = getattr(block, "text", None)
        if text:
            return text
    return result


async def _call(client, name, args):
    result = await client.call_tool(name, args)
    return _result_data(result)


async def run() -> None:
    async with Client(MCP_URL, auth=AUTH["Authorization"].split()[1]) as client:
        # 1. Tool listing
        tools = {t.name for t in await client.list_tools()}
        _say(f"list_tools -> {len(tools)} tools")
        missing = PINNED - tools
        assert not missing, f"missing pinned tools: {sorted(missing)}"
        _say("pinned tool set present")

        # 2. Search over the full upstream catalog
        search = await _call(client, "search_tools", {"query": "decompile function"})
        assert search, "search_tools returned nothing"
        _say("search_tools('decompile function') returned results")

        # 3. Server + project state
        status = await _call(client, "lab_server_status", {})
        _say(f"lab_server_status -> {status}")

        # 4. Upload a sample through the facade's own PUT endpoint
        start = await _call(
            client,
            "lab_sample_upload_start",
            {"filename": "smoketrue", "collection": "smoke"},
        )
        sample_id = start["sample_id"]
        put_url = start["upload_url"]
        if put_url.startswith(settings.public_base_url):
            put_url = f"{BASE}{put_url[len(settings.public_base_url):]}"
        with open(SAMPLE_BINARY, "rb") as fh:
            payload = fh.read()
        req = urllib.request.Request(put_url, data=payload, method="PUT")
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (loopback)
            assert resp.status == 200, f"upload failed: {resp.status}"
        _say(f"uploaded {len(payload)} bytes as sample {sample_id}")

        # 5. Import into the server repository (runs in the background) and poll
        started = await _call(
            client, "lab_repository_import_sample",
            {"sample_id": sample_id, "folder": "smoke", "checkout_for_edit": True},
        )
        assert started.get("status") == "running", f"expected 'running', got {started}"
        program_path = started["program_path"]
        _say(f"import started (background) for {program_path}; polling…")
        status = {}
        for _ in range(150):  # up to ~5 minutes
            status = await _call(client, "lab_repository_import_status", {"sample_id": sample_id})
            if status.get("status") in ("complete", "failed"):
                break
            await asyncio.sleep(2)
        assert status.get("status") == "complete", f"import did not complete: {status}"
        repo_import = status["result"]
        server_import = repo_import["server_import"]
        assert server_import.get("success"), f"server import failed: {server_import}"
        load = repo_import.get("load_after_import") or {}
        assert "error" not in load, f"load after import failed: {load}"
        _say(f"imported + loaded {program_path}")

        program_name = program_path.rsplit("/", 1)[-1]

        # 6. Exercise an upstream read tool, and find a function to annotate
        funcs = await _call(
            client, "call_tool",
            {"name": "list_functions", "arguments": {"program": program_name}},
        )
        funcs_text = str(funcs)
        _say(f"call_tool(list_functions) -> {funcs_text[:160]}")
        match = re.search(r"FUN_[0-9A-Fa-f]+", funcs_text)
        assert match, "no FUN_ function found to rename"
        target = match.group(0)

        # 7. Annotate: rename a function (the persistence test)
        renamed = await _call(
            client, "call_tool",
            {"name": "rename_function",
             "arguments": {"oldName": target, "newName": "AgentSmokeRenamed", "program": program_name}},
        )
        _say(f"rename {target} -> AgentSmokeRenamed: {str(renamed)[:140]}")

        # 8. Check in -> a new server version the GUI user can open
        checkin = await _call(
            client, "lab_repository_checkin",
            {"path": program_path, "comment": "smoke: agent rename"},
        )
        cdata = checkin.get("checkin", checkin)
        _say(f"checkin -> {cdata}")
        assert cdata.get("checked_in"), f"checkin did not commit changes: {cdata}"

        # 9. Confirm persistence: the repository file now shows version >= 2
        listing = await _call(client, "lab_repository_list", {"path": "/smoke"})
        files = (listing.get("files") or {}).get("files") or []
        entry = next((f for f in files if f.get("name") == program_name), None)
        assert entry, f"{program_name} not found in repository listing: {listing}"
        version = entry.get("version")
        assert version and int(version) >= 2, f"expected version >= 2 after edit+checkin, got {version}"
        _say(f"persisted: {program_name} is now version {version}")

    _say("ALL CHECKS PASSED")


def main() -> None:
    try:
        asyncio.run(run())
    except AssertionError as exc:
        _say(f"FAILED: {exc}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        _say(f"ERROR: {type(exc).__name__}: {exc}")
        sys.exit(2)


if __name__ == "__main__":
    main()
