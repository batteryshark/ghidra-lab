from __future__ import annotations

from pathlib import Path
from typing import Any

import requests


class GhidraRestError(RuntimeError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


class GhidraRestClient:
    """Thin client over the upstream GhidraMCP headless REST server.

    Every method maps to a stock upstream endpoint — there is no custom Java in
    the headless server. Server-bound project lifecycle is handled out of band
    by the one-time bootstrap; here we only open it and drive version control.
    """

    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------ programs

    def check_connection(self) -> dict[str, Any]:
        return self._get("/check_connection", timeout=10)

    def load_program(
        self,
        file_path: str | Path,
        language: str | None = None,
        compiler_spec: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"file": str(file_path)}
        if language:
            payload["language"] = language
        if compiler_spec:
            payload["compiler_spec"] = compiler_spec
        return self._post("/load_program", payload)

    def load_program_from_project(self, path: str) -> dict[str, Any]:
        return self._post("/load_program_from_project", {"path": path})

    def save_program(self, program: str | None = None) -> dict[str, Any]:
        params = {"program": program} if program else None
        return self._get("/save_program", params=params)

    # ------------------------------------------------------------------ projects

    def open_project(self, path: str) -> dict[str, Any]:
        return self._post("/open_project", {"path": path})

    def get_project_info(self) -> dict[str, Any]:
        return self._get("/get_project_info")

    # -------------------------------------------------------------------- server

    def server_status(self) -> dict[str, Any]:
        return self._get("/server/status")

    def server_connect(self, host: str, port: int) -> dict[str, Any]:
        return self._post("/server/connect", {"host": host, "port": port})

    def list_repositories(self) -> dict[str, Any]:
        return self._get("/server/repositories")

    def list_repository_files(self, repository: str, path: str = "/") -> dict[str, Any]:
        return self._get("/server/repository/files", params={"repo": repository, "path": path})

    def server_checkouts(self, repository: str, path: str) -> dict[str, Any]:
        return self._get("/server/checkouts", params={"repo": repository, "path": path})

    # ------------------------------------------------------------ version control
    # These drive the project DomainFile API via the Ghidra Lab service so the
    # checked-out program is genuinely writable and check-in creates a new server
    # version (upstream's /server/version_control/* cannot — see ghidra-mcp #119).

    def lab_checkout(self, path: str, exclusive: bool = True) -> dict[str, Any]:
        return self._post("/lab/checkout", {"path": path, "exclusive": exclusive})

    def lab_checkin(
        self,
        path: str,
        comment: str = "Agent analysis update",
        keep_checked_out: bool = False,
        save_before_checkin: bool = True,
    ) -> dict[str, Any]:
        return self._post(
            "/lab/checkin",
            {
                "path": path,
                "comment": comment,
                "keep_checked_out": keep_checked_out,
                "save_before_checkin": save_before_checkin,
            },
        )

    def lab_undo_checkout(self, path: str, keep: bool = False) -> dict[str, Any]:
        return self._post("/lab/undo_checkout", {"path": path, "keep": keep})

    # ------------------------------------------------------------------- helpers

    def _get(self, path: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        response = requests.get(
            f"{self.base_url}{path}",
            params=params,
            timeout=timeout or self.timeout,
        )
        return self._decode(response)

    def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(f"{self.base_url}{path}", json=json, timeout=self.timeout)
        return self._decode(response)

    def _decode(self, response: requests.Response) -> dict[str, Any]:
        text = response.text
        try:
            payload = response.json()
        except ValueError:
            # Some endpoints return a plain-text status body; surface it rather
            # than failing, but still honor HTTP error codes below.
            payload = {"raw": text}

        if response.status_code >= 400:
            message = payload.get("error") or payload.get("message") or text
            raise GhidraRestError(f"Ghidra REST HTTP {response.status_code}: {message}", payload)
        if isinstance(payload, dict) and payload.get("success") is False:
            raise GhidraRestError(payload.get("error") or payload.get("message") or str(payload), payload)
        return payload if isinstance(payload, dict) else {"result": payload}
