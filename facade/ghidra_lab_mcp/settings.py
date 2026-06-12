from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    token: str
    insecure: bool
    sample_root: Path
    public_base_url: str
    ghidra_rest_url: str
    ghidra_bridge_mcp_url: str
    ghidra_server_host: str
    ghidra_server_port: int
    ghidra_server_user: str
    ghidra_server_password: str
    default_repository: str
    shared_project_root: Path
    shared_project_name: str
    shared_project_gpr: Path
    analyze_headless_path: Path
    analyze_headless_timeout: int
    analysis_timeout_per_file: int
    search_results: int
    max_upload_bytes: int
    request_timeout: float


def load_settings() -> Settings:
    insecure = _bool_env("GHIDRA_LAB_INSECURE", False)
    token = os.getenv("GHIDRA_LAB_TOKEN", "")
    if not token and not insecure:
        raise RuntimeError("GHIDRA_LAB_TOKEN is required unless GHIDRA_LAB_INSECURE=1")

    default_repository = os.getenv("GHIDRA_LAB_DEFAULT_REPOSITORY", "GhidraLab")
    shared_project_root = Path(os.getenv("GHIDRA_LAB_SHARED_PROJECT_ROOT", "/data/projects"))
    shared_project_name = f"{default_repository}_agent"

    return Settings(
        host=os.getenv("GHIDRA_LAB_HOST", "0.0.0.0"),
        port=_int_env("GHIDRA_LAB_PORT", 8080),
        token=token,
        insecure=insecure,
        sample_root=Path(os.getenv("GHIDRA_LAB_SAMPLE_ROOT", "/data/uploads")),
        public_base_url=os.getenv("GHIDRA_LAB_PUBLIC_BASE_URL", "http://127.0.0.1:18080").rstrip("/"),
        ghidra_rest_url=os.getenv("GHIDRA_REST_URL", "http://127.0.0.1:8089").rstrip("/"),
        ghidra_bridge_mcp_url=os.getenv("GHIDRA_BRIDGE_MCP_URL", "http://127.0.0.1:8081/mcp"),
        ghidra_server_host=os.getenv("GHIDRA_SERVER_HOST", "127.0.0.1"),
        ghidra_server_port=_int_env("GHIDRA_SERVER_PORT", 13100),
        ghidra_server_user=os.getenv("GHIDRA_SERVER_USER", ""),
        ghidra_server_password=os.getenv("GHIDRA_SERVER_PASSWORD", ""),
        default_repository=default_repository,
        shared_project_root=shared_project_root,
        shared_project_name=shared_project_name,
        shared_project_gpr=shared_project_root / f"{shared_project_name}.gpr",
        analyze_headless_path=Path(os.getenv("GHIDRA_ANALYZE_HEADLESS", "/opt/ghidra/support/analyzeHeadless")),
        analyze_headless_timeout=_int_env("GHIDRA_LAB_ANALYZE_HEADLESS_TIMEOUT", 1800),
        analysis_timeout_per_file=_int_env("GHIDRA_LAB_ANALYSIS_TIMEOUT_PER_FILE", 0),
        search_results=_int_env("GHIDRA_LAB_SEARCH_RESULTS", 8),
        max_upload_bytes=_int_env("GHIDRA_LAB_MAX_UPLOAD_BYTES", 4 * 1024 * 1024 * 1024),
        request_timeout=float(os.getenv("GHIDRA_LAB_REQUEST_TIMEOUT", "300")),
    )


settings = load_settings()
