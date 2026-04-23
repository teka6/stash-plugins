"""Whisparr REST client (stdlib urllib). Variant A: monitor-flag only, no ImportList management."""
from typing import Optional

from .common import http_json


class WhisparrClient:
    def __init__(self, url: str, api_key: str):
        self.base = f"{url.rstrip('/')}/api/v3"
        self._headers = {"X-Api-Key": api_key}

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        return http_json(
            f"{self.base}{path}",
            method=method,
            headers=self._headers,
            json_body=body,
        )

    def ping(self) -> bool:
        """Lightweight reachability check; raises on failure."""
        self._request("GET", "/system/status")
        return True

    # ---------------- performer ----------------

    def get_all_performers(self) -> list:
        result = self._request("GET", "/performer")
        return result if isinstance(result, list) else []

    def set_monitored(self, performer_obj: dict, monitored: bool) -> bool:
        """Set monitored on a Whisparr performer object. Returns True if a write was made,
        False if already in desired state. Requires the full object (PUT needs full body)."""
        if performer_obj.get("monitored") == monitored:
            return False
        performer_obj["monitored"] = monitored
        self._request("PUT", f"/performer/{performer_obj['id']}", performer_obj)
        return True
