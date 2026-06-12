from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .settings import settings


SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def clean_name(value: str, default: str) -> str:
    value = SEGMENT_RE.sub("_", (value or "").strip()).strip("._-")
    return value or default


def normalize_folder(folder: str | None) -> str:
    clean = (folder or "/").strip()
    if not clean or clean == "/":
        return "/"
    return "/" + "/".join(clean_name(part, "folder") for part in clean.strip("/").split("/") if part)


def repository_url(repository: str, folder: str | None = None) -> str:
    suffix = normalize_folder(folder)
    base = f"ghidra://{settings.ghidra_server_host}:{settings.ghidra_server_port}/{repository}"
    if suffix != "/":
        base += suffix
    return base


def import_to_repository(
    file_path: str | Path,
    repository: str,
    folder: str | None = None,
    language: str | None = None,
    compiler_spec: str | None = None,
    analyze: bool = True,
    overwrite: bool = False,
    commit_comment: str | None = None,
) -> dict[str, Any]:
    if not settings.ghidra_server_user or not settings.ghidra_server_password:
        raise RuntimeError("GHIDRA_SERVER_USER and GHIDRA_SERVER_PASSWORD are required for server imports")
    if not settings.analyze_headless_path.exists():
        raise RuntimeError(f"analyzeHeadless not found at {settings.analyze_headless_path}")

    repo_url = repository_url(repository, folder)
    log_dir = settings.sample_root.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "analyze-headless-import.log"

    args = [
        str(settings.analyze_headless_path),
        repo_url,
        "-import",
        str(file_path),
        "-connect",
        settings.ghidra_server_user,
        "-p",
        "-commit",
        commit_comment or "Agent sample import",
        "-log",
        str(log_path),
    ]
    if not analyze:
        args.append("-noanalysis")
    if overwrite:
        args.append("-overwrite")
    if language:
        args.extend(["-processor", language])
    if compiler_spec:
        args.extend(["-cspec", compiler_spec])
    if settings.analysis_timeout_per_file > 0:
        args.extend(["-analysisTimeoutPerFile", str(settings.analysis_timeout_per_file)])

    completed = subprocess.run(
        args,
        input=f"{settings.ghidra_server_password}\n",
        text=True,
        capture_output=True,
        timeout=settings.analyze_headless_timeout,
        check=False,
    )
    output = (completed.stdout + "\n" + completed.stderr).strip()
    tail = "\n".join(output.splitlines()[-80:])
    result = {
        "success": completed.returncode == 0,
        "returncode": completed.returncode,
        "repository_url": repo_url,
        "repository": repository,
        "folder": normalize_folder(folder),
        "log_path": str(log_path),
        "output_tail": tail,
        "command": [
            str(settings.analyze_headless_path),
            repo_url,
            "-import",
            str(file_path),
            "-connect",
            settings.ghidra_server_user,
            "-p",
            "-commit",
            commit_comment or "Agent sample import",
        ],
    }
    if completed.returncode != 0:
        raise RuntimeError(f"analyzeHeadless import failed with exit {completed.returncode}: {tail}")
    return result
