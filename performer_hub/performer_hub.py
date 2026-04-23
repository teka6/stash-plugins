#!/usr/bin/env python3
"""Performer Hub — Stash plugin entry point.

Dispatches one of:
  - Hook   Performer.Update.Post      → sync.handle_hook
  - Task   mode=status                → _handle_status
  - Task   mode=sync-all              → sync.handle_sync_all
  - Task   mode=sync-report           → sync.handle_report
  - Task   mode=sync-monitor-whisparr → sync.handle_monitor_whisparr_bulk
  - Task   mode=enrich                → enrich.handle_enrich
  - Task   mode=ui-performer-scene-count → _handle_ui_performer_count
  - Task   mode=ui-studio-scene-count    → _handle_ui_studio_count
"""
import json
import sys
from typing import Optional

from lib.common import (
    STASHDB_ENDPOINT_MARKER,
    log,
    read_plugin_input,
    write_plugin_output,
)
from lib.stash import StashClient
from lib.stashdb import StashDBClient
from lib.whisparr import WhisparrClient
from lib import sync as sync_mod
from lib import enrich as enrich_mod


# ============================== helpers ==============================

def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return default


def _require(client, label: str) -> None:
    if not client:
        raise RuntimeError(f"{label} not configured — set it in Stash → Settings → Plugins → Performer Hub")


def _discover_stashdb_endpoint(stash: StashClient) -> Optional[str]:
    try:
        boxes = stash.get_stash_boxes()
    except Exception as e:
        log(f"could not read stash-box config: {e}", "warning")
        return None
    for box in boxes:
        if STASHDB_ENDPOINT_MARKER in (box.get("endpoint") or "").lower():
            return box.get("endpoint")
    return None


def _build_clients(stash: StashClient):
    """Returns (stashdb, whisparr, cfg, stashdb_endpoint)."""
    cfg = stash.get_plugin_settings()
    stashdb_endpoint = _discover_stashdb_endpoint(stash)

    stashdb: Optional[StashDBClient] = None
    api_key = cfg.get("stashdb_api_key")
    if api_key and stashdb_endpoint:
        stashdb = StashDBClient(api_key=api_key, endpoint=stashdb_endpoint)
        log(f"using StashDB endpoint: {stashdb_endpoint}")
    elif api_key:
        log("stashdb_api_key set but no stashdb.org stash-box configured in Stash", "warning")

    whisparr: Optional[WhisparrClient] = None
    w_url = cfg.get("whisparr_url")
    w_key = cfg.get("whisparr_api_key")
    if w_url and w_key:
        whisparr = WhisparrClient(url=w_url, api_key=w_key)
        log(f"using Whisparr: {w_url}")

    return stashdb, whisparr, cfg, stashdb_endpoint


# ============================== status handler ==============================

def _handle_status(stash: StashClient, stashdb: Optional[StashDBClient],
                   whisparr: Optional[WhisparrClient]) -> dict:
    stats = stash.get_stats()
    favs = stash.find_favorite_performers()
    with_stashdb_uuid = sum(
        1 for p in favs
        if any(STASHDB_ENDPOINT_MARKER in (s.get("endpoint") or "")
               for s in p.get("stash_ids") or [])
    )

    result = {
        "stash": {
            "performers_total": stats.get("performer_count", 0),
            "favorites_total": len(favs),
            "favorites_with_stashdb_uuid": with_stashdb_uuid,
        },
        "stashdb": {"connected": False},
        "whisparr": {"connected": False},
    }

    if stashdb:
        try:
            db_favs = stashdb.get_favorite_performers()
            result["stashdb"] = {"connected": True, "favorites_total": len(db_favs)}
        except Exception as e:
            result["stashdb"] = {"connected": False, "error": str(e)}
    if whisparr:
        try:
            wp = whisparr.get_all_performers()
            result["whisparr"] = {
                "connected": True,
                "performers_total": len(wp),
                "monitored_total": sum(1 for p in wp if p.get("monitored")),
            }
        except Exception as e:
            result["whisparr"] = {"connected": False, "error": str(e)}

    log(f"status:\n{json.dumps(result, indent=2)}")
    return result


# ============================== UI scene-count handlers ==============================

def _handle_ui_performer_count(stash: StashClient, args: dict, stashdb_api_key: str) -> str:
    endpoint = args.get("endpoint")
    stash_id = args.get("stash_id")
    api_key = args.get("api_key") or stashdb_api_key
    if not (endpoint and stash_id and api_key):
        log("ui-performer-scene-count: missing endpoint/stash_id/api_key", "error")
        return "error"

    sdb = StashDBClient(api_key=api_key, endpoint=endpoint)
    try:
        remote_count = sdb.count_performer_scenes(stash_id)
    except Exception as e:
        log(f"{stash_id}: stashbox error ({e})", "error")
        return "error"

    local_count = 0
    try:
        for p in stash.find_performers_linked_to_endpoint(endpoint):
            if any(s.get("stash_id") == stash_id for s in p.get("stash_ids") or []):
                local_count = stash.count_performer_scenes_on_endpoint(p["id"], endpoint)
                break
    except Exception as e:
        log(f"local performer-count lookup failed: {e}", "warning")

    # UI scrapes this log line: "<stash_id>: <local>/<stashbox>"
    log(f"{stash_id}: {local_count}/{remote_count}")
    return "ok"


def _handle_ui_studio_count(stash: StashClient, args: dict, stashdb_api_key: str,
                            include_subsidiaries: bool) -> str:
    endpoint = args.get("endpoint")
    stash_id = args.get("stash_id")
    api_key = args.get("api_key") or stashdb_api_key
    if not (endpoint and stash_id and api_key):
        log("ui-studio-scene-count: missing endpoint/stash_id/api_key", "error")
        return "error"

    sdb = StashDBClient(api_key=api_key, endpoint=endpoint)
    try:
        remote_count = sdb.count_studio_scenes(stash_id, include_subsidiaries)
    except Exception as e:
        log(f"{stash_id}: stashbox error ({e})", "error")
        return "error"

    local_count = 0
    try:
        for s in stash.find_studios_linked_to_endpoint(endpoint):
            if any(sid.get("stash_id") == stash_id for sid in s.get("stash_ids") or []):
                local_count = stash.count_studio_scenes_on_endpoint(s["id"], endpoint, include_subsidiaries)
                break
    except Exception as e:
        log(f"local studio-count lookup failed: {e}", "warning")

    log(f"{stash_id}: {local_count}/{remote_count}")
    return "ok"


# ============================== main dispatch ==============================

def main() -> None:
    try:
        payload = read_plugin_input()
        server_connection = payload.get("server_connection") or {}
        args = payload.get("args") or {}

        stash = StashClient(server_connection)
        stashdb, whisparr, cfg, stashdb_endpoint = _build_clients(stash)

        dry_run_sync = _as_bool(cfg.get("dry_run_sync"), default=True)
        dry_run_enrich = _as_bool(cfg.get("dry_run_enrich"), default=True)
        try:
            rate_limit = float(cfg.get("rate_limit_seconds") or 2)
        except (TypeError, ValueError):
            rate_limit = 2.0
        include_subsidiaries = _as_bool(cfg.get("include_subsidiary_studios"), default=False)

        # ----- Hook dispatch -----
        hookctx = args.get("hookContext")
        if hookctx:
            htype = hookctx.get("type") or ""
            pid = hookctx.get("id") or ""
            log(f"hook fired: {htype} id={pid}")
            if not stashdb:
                log("hook: StashDB not configured — skipping", "warning")
                write_plugin_output("hook skipped (no StashDB configured)")
                return
            if not pid:
                log("hook fired without performer id — skipping", "warning")
                write_plugin_output("hook skipped (no performer id)")
                return
            sync_mod.handle_hook(stash, stashdb, whisparr, pid, dry_run_sync)
            write_plugin_output("hook processed")
            return

        # ----- Task dispatch -----
        mode = args.get("mode") or "status"
        log(f"task mode: {mode}")

        if mode == "status":
            result = _handle_status(stash, stashdb, whisparr)

        elif mode == "sync-all":
            _require(stashdb, "StashDB")
            result = sync_mod.handle_sync_all(stash, stashdb, whisparr, dry_run_sync)

        elif mode == "sync-report":
            _require(stashdb, "StashDB")
            result = sync_mod.handle_report(stash, stashdb, whisparr)

        elif mode == "sync-monitor-whisparr":
            _require(stashdb, "StashDB")
            _require(whisparr, "Whisparr")
            result = sync_mod.handle_monitor_whisparr_bulk(stash, stashdb, whisparr, dry_run_sync)

        elif mode == "enrich":
            _require(stashdb, "StashDB")
            if not stashdb_endpoint:
                raise RuntimeError("no StashDB endpoint configured in Stash's stash-box settings")
            all_performers = _as_bool(args.get("allPerformers"), default=False)
            result = enrich_mod.handle_enrich(
                stash, stashdb, stashdb_endpoint,
                dry_run_enrich, all_performers, rate_limit,
            )

        elif mode == "ui-performer-scene-count":
            result = _handle_ui_performer_count(stash, args, cfg.get("stashdb_api_key") or "")

        elif mode == "ui-studio-scene-count":
            result = _handle_ui_studio_count(
                stash, args, cfg.get("stashdb_api_key") or "", include_subsidiaries,
            )

        else:
            raise RuntimeError(f"unknown mode: {mode}")

        write_plugin_output(output=result)
    except Exception as e:
        log(f"fatal: {e}", "error")
        write_plugin_output(error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
