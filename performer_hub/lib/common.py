"""Shared utilities for Performer Hub — plugin I/O, logging, HTTP defaults."""
import json
import ssl
import sys
import urllib.error
import urllib.request
from typing import Optional

PLUGIN_ID = "performer_hub"
VERSION = "0.2.0"
USER_AGENT = f"{PLUGIN_ID}/{VERSION}"

STASHDB_ENDPOINT_DEFAULT = "https://stashdb.org/graphql"
STASHDB_ENDPOINT_MARKER = "stashdb.org"
TPDB_ENDPOINT_MARKER = "theporndb"

HTTP_TIMEOUT = 60

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


# Stash plugin log protocol: \x01<level>\x02<message>\n on stderr.
# Level codes: t=trace, d=debug, i=info, w=warning, e=error, p=progress
_LOG_LEVELS = {
    "trace": "t",
    "debug": "d",
    "info": "i",
    "warning": "w",
    "error": "e",
    "progress": "p",
}


def log(msg: str, level: str = "info") -> None:
    code = _LOG_LEVELS.get(level, "i")
    for line in str(msg).split("\n"):
        if line.strip():
            sys.stderr.write(f"\x01{code}\x02{line}\n")
    sys.stderr.flush()


def read_plugin_input() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw else {}
    except Exception as e:
        log(f"failed to parse plugin input: {e}", "error")
        return {}


def write_plugin_output(output=None, error: Optional[str] = None) -> None:
    sys.stdout.write(json.dumps({"output": output, "error": error}))
    sys.stdout.flush()


def http_request(
    url: str,
    method: str = "GET",
    headers: Optional[dict] = None,
    body: Optional[bytes] = None,
    timeout: int = HTTP_TIMEOUT,
) -> bytes:
    """Thin urllib wrapper. Returns raw response bytes. Raises RuntimeError with HTTP detail on error."""
    merged_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        merged_headers.update(headers)
    req = urllib.request.Request(url, data=body, method=method, headers=merged_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {url}: {detail}") from None


def http_json(
    url: str,
    method: str = "GET",
    headers: Optional[dict] = None,
    json_body: Optional[dict] = None,
    timeout: int = HTTP_TIMEOUT,
) -> dict:
    """POST/GET JSON, parse JSON response. Returns {} on empty body."""
    body = json.dumps(json_body).encode() if json_body is not None else None
    merged = dict(headers or {})
    if json_body is not None:
        merged["Content-Type"] = "application/json"
    raw = http_request(url, method=method, headers=merged, body=body, timeout=timeout)
    return json.loads(raw) if raw else {}
