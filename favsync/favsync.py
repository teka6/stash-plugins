#!/usr/bin/env python3
"""
FavSync — Bidirectional performer favorites sync across Stash, StashDB, and Whisparr.

Runs as a Stash plugin. Supports:
  - Hook: Performer.Update.Post — auto-sync favorite toggle
  - Task: mode=sync-all        — union reconcile all three sides
  - Task: mode=report          — dry-run report of mismatches
  - Task: mode=monitor-whisparr — bulk set monitored=True on Whisparr performers matching Stash/StashDB favorites

No DB access. All communication via HTTP APIs (GraphQL for Stash & StashDB, REST for Whisparr).
Requires Python 3.9+ (stdlib only).
"""
import json
import os
import sys
import ssl
import time
import urllib.request
import urllib.error
from typing import Optional, Iterable

PLUGIN_ID = "favsync"
VERSION = "0.1.0"

UA = f"favsync/{VERSION}"
SDB_URL = "https://stashdb.org/graphql"
SDB_ENDPOINT_MARKER = "stashdb.org"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

TIMEOUT = 60


# ============================== logging / io ==============================

def log(level: str, msg: str) -> None:
    """Stash plugin log convention: stderr with level prefix."""
    sys.stderr.write(f"{level}: {msg}\n")
    sys.stderr.flush()


def emit_output(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


def read_plugin_input() -> dict:
    global _CACHED_STDIN
    try:
        raw = sys.stdin.read()
        _CACHED_STDIN = json.loads(raw) if raw else {}
        return _CACHED_STDIN
    except Exception as e:
        log("error", f"failed to parse plugin input: {e}")
        _CACHED_STDIN = {}
        return {}


# ============================== Stash (local) ==============================

_CACHED_STDIN: Optional[dict] = None


def _stash_base_and_cookie() -> tuple:
    """Stash raw-interface plugins receive server_connection via stdin JSON, not env."""
    global _CACHED_STDIN
    sc = (_CACHED_STDIN or {}).get("server_connection") or {}
    scheme = sc.get("Scheme", "http") or "http"
    host = sc.get("Host", "0.0.0.0")
    if host in ("0.0.0.0", ""):
        host = "localhost"
    port = sc.get("Port", 9999)
    cookie = sc.get("SessionCookie") or {}
    base = f"{scheme}://{host}:{port}"
    return base, cookie


def stash_gql(query: str, variables: Optional[dict] = None) -> dict:
    base, cookie = _stash_base_and_cookie()
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        f"{base}/graphql",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    if cookie and cookie.get("Name"):
        req.add_header("Cookie", f"{cookie['Name']}={cookie['Value']}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Stash HTTP {e.code}: {e.read()[:300].decode('utf8', errors='replace')}")
    if "errors" in data and data["errors"]:
        raise RuntimeError(f"Stash GraphQL errors: {data['errors']}")
    return data.get("data") or {}


def get_plugin_settings() -> dict:
    q = "{ configuration { plugins } }"
    data = stash_gql(q)
    plugins = (data.get("configuration") or {}).get("plugins") or {}
    return plugins.get(PLUGIN_ID) or {}


# ============================== StashDB ==============================

def stashdb_gql(api_key: str, query: str, variables: Optional[dict] = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        SDB_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "ApiKey": api_key,
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"StashDB HTTP {e.code}: {e.read()[:300].decode('utf8', errors='replace')}")
    if "errors" in data and data["errors"]:
        raise RuntimeError(f"StashDB GraphQL errors: {data['errors']}")
    return data.get("data") or {}


# ============================== Whisparr ==============================

def whisparr_req(url: str, api_key: str, method: str = "GET", body: Optional[dict] = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "X-Api-Key": api_key,
        "Accept": "application/json",
        "User-Agent": UA,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Whisparr HTTP {e.code}: {e.read()[:300].decode('utf8', errors='replace')}")
    return json.loads(raw) if raw else {}


# ============================== settings / validation ==============================

def validated_config() -> dict:
    cfg = get_plugin_settings()
    missing = [k for k in ("stashdb_api_key", "whisparr_url", "whisparr_api_key") if not cfg.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing plugin settings: {missing}. Configure them in Stash → Settings → Plugins → FavSync."
        )
    cfg["whisparr_url"] = cfg["whisparr_url"].rstrip("/")
    cfg["dry_run"] = bool(cfg.get("dry_run"))
    return cfg


# ============================== data fetching ==============================

def stash_fetch_favorites() -> list:
    """Return [{id, name, favorite, stashdb_uuid}] for all Stash performers that have a StashDB UUID."""
    q = """
    query {
      findPerformers(
        performer_filter: { stash_id_endpoint: { endpoint: "https://stashdb.org/graphql", modifier: NOT_NULL } }
        filter: { per_page: -1 }
      ) {
        count
        performers {
          id
          name
          favorite
          stash_ids { endpoint stash_id }
        }
      }
    }
    """
    data = stash_gql(q)
    out = []
    for p in (data.get("findPerformers") or {}).get("performers") or []:
        uuid = None
        for s in p.get("stash_ids") or []:
            if SDB_ENDPOINT_MARKER in (s.get("endpoint") or ""):
                uuid = s.get("stash_id")
                break
        if uuid:
            out.append({
                "id": p["id"],
                "name": p["name"],
                "favorite": bool(p.get("favorite")),
                "stashdb_uuid": uuid,
            })
    return out


def stashdb_fetch_favorites(api_key: str) -> list:
    """Return [{id, name}] for current StashDB favorites of the authenticated user."""
    q = """
    query($page: Int!) {
      queryPerformers(input: { is_favorite: true, per_page: 100, page: $page }) {
        count
        performers { id name }
      }
    }
    """
    out = []
    page = 1
    while True:
        data = stashdb_gql(api_key, q, {"page": page})
        block = data.get("queryPerformers") or {}
        items = block.get("performers") or []
        if not items:
            break
        out.extend({"id": x["id"], "name": x["name"]} for x in items)
        if len(items) < 100:
            break
        page += 1
        if page > 200:  # safety
            break
    return out


def whisparr_fetch_all_performers(url: str, api_key: str) -> list:
    return whisparr_req(f"{url}/api/v3/performer", api_key)


# ============================== mutations ==============================

def stashdb_set_favorite(api_key: str, performer_uuid: str, favorite: bool) -> str:
    """Returns: 'set', 'already', 'error'."""
    q = """
    mutation($id: ID!, $fav: Boolean!) {
      favoritePerformer(id: $id, favorite: $fav)
    }
    """
    try:
        stashdb_gql(api_key, q, {"id": performer_uuid, "fav": favorite})
        return "set"
    except RuntimeError as e:
        msg = str(e)
        # "duplicate key value violates unique constraint" = already favorited (idempotent)
        if "duplicate key" in msg or "unique constraint" in msg:
            return "already"
        raise


def stash_set_favorite(performer_id: str, favorite: bool) -> None:
    q = """
    mutation($id: ID!, $fav: Boolean!) {
      performerUpdate(input: { id: $id, favorite: $fav }) { id favorite }
    }
    """
    stash_gql(q, {"id": performer_id, "fav": favorite})


def whisparr_set_monitored(wp_url: str, wp_key: str, performer_obj: dict, monitored: bool) -> None:
    if performer_obj.get("monitored") == monitored:
        return
    performer_obj["monitored"] = monitored
    whisparr_req(f"{wp_url}/api/v3/performer/{performer_obj['id']}", wp_key, "PUT", performer_obj)


# ============================== handlers ==============================

def handle_hook(hookcontext: dict, cfg: dict) -> None:
    pid = hookcontext.get("id")
    if not pid:
        log("warning", "hook fired without performer id")
        return

    q = """
    query($id: ID!) {
      findPerformer(id: $id) {
        id name favorite
        stash_ids { endpoint stash_id }
      }
    }
    """
    data = stash_gql(q, {"id": pid})
    p = data.get("findPerformer")
    if not p:
        log("warning", f"hook: performer {pid} not found")
        return

    uuid = None
    for s in p.get("stash_ids") or []:
        if SDB_ENDPOINT_MARKER in (s.get("endpoint") or ""):
            uuid = s.get("stash_id"); break
    if not uuid:
        log("info", f"hook: {p.get('name')} has no StashDB UUID — skipping")
        return

    favorite = bool(p.get("favorite"))
    log("info", f"hook: {p.get('name')} favorite={favorite} uuid={uuid}")

    # StashDB
    try:
        if cfg["dry_run"]:
            log("info", f"  [dry] would set StashDB favorite={favorite}")
        else:
            stashdb_set_favorite(cfg["stashdb_api_key"], uuid, favorite)
            log("info", f"  StashDB favorite={favorite} ✓")
    except Exception as e:
        log("error", f"  StashDB sync failed: {e}")

    # Whisparr
    try:
        perfs = whisparr_fetch_all_performers(cfg["whisparr_url"], cfg["whisparr_api_key"])
        match = next((x for x in perfs if x.get("foreignId") == uuid), None)
        if not match:
            log("warning", f"  Whisparr: no performer matches UUID {uuid}")
            return
        if match.get("monitored") == favorite:
            log("info", f"  Whisparr: monitored already {favorite}")
            return
        if cfg["dry_run"]:
            log("info", f"  [dry] would set Whisparr monitored={favorite}")
        else:
            whisparr_set_monitored(cfg["whisparr_url"], cfg["whisparr_api_key"], match, favorite)
            log("info", f"  Whisparr monitored={favorite} ✓")
    except Exception as e:
        log("error", f"  Whisparr sync failed: {e}")


def _build_target_set(stash_favs: list, sdb_favs: list) -> dict:
    """Target state = union. Returns {uuid: {name, in_stash, in_stashdb}}."""
    target = {}
    for p in stash_favs:
        if p["favorite"]:
            target[p["stashdb_uuid"]] = {
                "name": p["name"],
                "stash_id": p["id"],
                "in_stash": True,
                "in_stashdb": False,
            }
    for s in sdb_favs:
        u = s["id"]
        if u in target:
            target[u]["in_stashdb"] = True
        else:
            target[u] = {"name": s["name"], "stash_id": None, "in_stash": False, "in_stashdb": True}
    return target


def handle_sync_all(cfg: dict, dry: bool = False) -> dict:
    log("info", "fetching Stash favorites…")
    stash_favs = stash_fetch_favorites()
    stash_fav_only = [p for p in stash_favs if p["favorite"]]
    log("info", f"  stash favorites: {len(stash_fav_only)} (of {len(stash_favs)} linked to StashDB)")

    log("info", "fetching StashDB favorites…")
    sdb_favs = stashdb_fetch_favorites(cfg["stashdb_api_key"])
    log("info", f"  stashdb favorites: {len(sdb_favs)}")

    log("info", "fetching Whisparr performers…")
    wp_perfs = whisparr_fetch_all_performers(cfg["whisparr_url"], cfg["whisparr_api_key"])
    wp_by_uuid = {x.get("foreignId"): x for x in wp_perfs if x.get("foreignId")}
    log("info", f"  whisparr performers: {len(wp_perfs)} ({len(wp_by_uuid)} with StashDB UUID)")

    target = _build_target_set(stash_fav_only, sdb_favs)
    log("info", f"  union target size: {len(target)}")

    summary = {
        "stash_favs": len(stash_fav_only),
        "stashdb_favs": len(sdb_favs),
        "union_target": len(target),
        "stash_updates": 0,
        "stashdb_updates": 0,
        "whisparr_monitored_set": 0,
        "whisparr_missing_performer": 0,
        "errors": 0,
    }

    summary["stashdb_already"] = 0
    for uuid, info in target.items():
        name = info["name"]

        # 1. StashDB: idempotent favorite (catch 'duplicate key' as already-favorited).
        #    StashDB's queryPerformers(is_favorite=true) filter is unreliable,
        #    so we ALWAYS push (fast no-op if already set).
        if dry:
            log("info", f"  [dry] STASHDB would ensure favorite for {name} ({uuid})")
        else:
            try:
                r = stashdb_set_favorite(cfg["stashdb_api_key"], uuid, True)
                if r == "set":
                    summary["stashdb_updates"] += 1
                    log("info", f"  STASHDB favorited {name}")
                elif r == "already":
                    summary["stashdb_already"] += 1
            except Exception as e:
                summary["errors"] += 1
                log("error", f"  STASHDB update failed for {name}: {e}")

        # 2. Stash: ensure favorite=True if not already
        if not info["in_stash"]:
            if dry:
                log("info", f"  [dry] STASH   would favorite {name} ({uuid})")
            else:
                try:
                    q = """
                    query($uuid: String!) {
                      findPerformers(
                        performer_filter: {
                          stash_id_endpoint: {
                            endpoint: "https://stashdb.org/graphql"
                            stash_id: $uuid
                            modifier: EQUALS
                          }
                        }
                        filter: { per_page: 1 }
                      ) { performers { id name } }
                    }
                    """
                    data = stash_gql(q, {"uuid": uuid})
                    hits = (data.get("findPerformers") or {}).get("performers") or []
                    if hits:
                        stash_set_favorite(hits[0]["id"], True)
                        summary["stash_updates"] += 1
                        log("info", f"  STASH   favorited {name} (stash_id={hits[0]['id']})")
                    else:
                        log("warning", f"  STASH   performer uuid={uuid} ({name}) not in Stash — must be added manually")
                except Exception as e:
                    summary["errors"] += 1
                    log("error", f"  STASH   update failed for {name}: {e}")

        # 3. Whisparr: ensure monitored=True if performer exists
        wp = wp_by_uuid.get(uuid)
        if not wp:
            summary["whisparr_missing_performer"] += 1
            continue
        if wp.get("monitored"):
            continue
        if dry:
            log("info", f"  [dry] WHISPARR would set monitored=True for {name}")
        else:
            try:
                whisparr_set_monitored(cfg["whisparr_url"], cfg["whisparr_api_key"], wp, True)
                summary["whisparr_monitored_set"] += 1
                log("info", f"  WHISPARR monitored=True for {name}")
            except Exception as e:
                summary["errors"] += 1
                log("error", f"  WHISPARR update failed for {name}: {e}")

    log("info", f"summary: {json.dumps(summary)}")
    return summary


def handle_monitor_whisparr_only(cfg: dict, dry: bool = False) -> dict:
    """Only set monitored=True on Whisparr performers matching Stash ∪ StashDB favorites. No Stash/StashDB writes."""
    log("info", "bulk-monitor-whisparr: fetching favorites…")
    stash_favs = stash_fetch_favorites()
    stash_uuids = {p["stashdb_uuid"] for p in stash_favs if p["favorite"]}
    sdb_favs = stashdb_fetch_favorites(cfg["stashdb_api_key"])
    sdb_uuids = {s["id"] for s in sdb_favs}
    union = stash_uuids | sdb_uuids
    log("info", f"  union size: {len(union)} (stash={len(stash_uuids)}, stashdb={len(sdb_uuids)})")

    wp_perfs = whisparr_fetch_all_performers(cfg["whisparr_url"], cfg["whisparr_api_key"])
    wp_by_uuid = {x.get("foreignId"): x for x in wp_perfs if x.get("foreignId")}

    summary = {
        "union_size": len(union),
        "already_monitored": 0,
        "set_monitored": 0,
        "missing_in_whisparr": 0,
        "errors": 0,
    }

    for uuid in union:
        wp = wp_by_uuid.get(uuid)
        if not wp:
            summary["missing_in_whisparr"] += 1
            continue
        if wp.get("monitored"):
            summary["already_monitored"] += 1
            continue
        if dry:
            log("info", f"  [dry] would set monitored=True for {wp.get('fullName')} ({uuid})")
            continue
        try:
            whisparr_set_monitored(cfg["whisparr_url"], cfg["whisparr_api_key"], wp, True)
            summary["set_monitored"] += 1
            log("info", f"  ✓ monitored=True for {wp.get('fullName')}")
        except Exception as e:
            summary["errors"] += 1
            log("error", f"  ✗ failed for {wp.get('fullName')}: {e}")

    log("info", f"summary: {json.dumps(summary)}")
    return summary


def handle_report(cfg: dict) -> dict:
    log("info", "REPORT mode — no writes")
    stash_favs = stash_fetch_favorites()
    stash_fav_only = [p for p in stash_favs if p["favorite"]]
    sdb_favs = stashdb_fetch_favorites(cfg["stashdb_api_key"])
    wp_perfs = whisparr_fetch_all_performers(cfg["whisparr_url"], cfg["whisparr_api_key"])
    wp_monitored = [x for x in wp_perfs if x.get("monitored")]

    stash_uuids = {p["stashdb_uuid"] for p in stash_fav_only}
    sdb_uuids = {s["id"] for s in sdb_favs}
    wp_mon_uuids = {x.get("foreignId") for x in wp_monitored if x.get("foreignId")}

    only_stash = stash_uuids - sdb_uuids
    only_stashdb = sdb_uuids - stash_uuids
    both = stash_uuids & sdb_uuids
    union = stash_uuids | sdb_uuids

    mon_needed = union - wp_mon_uuids
    mon_extra = wp_mon_uuids - union

    summary = {
        "stash_favs": len(stash_fav_only),
        "stashdb_favs": len(sdb_favs),
        "whisparr_monitored": len(wp_monitored),
        "in_both_stash_stashdb": len(both),
        "only_in_stash": len(only_stash),
        "only_in_stashdb": len(only_stashdb),
        "whisparr_monitored_missing_for_favs": len(mon_needed),
        "whisparr_monitored_not_in_favs": len(mon_extra),
    }
    log("info", f"REPORT summary: {json.dumps(summary, indent=2)}")
    # Sample a few
    if only_stash:
        sample = list(only_stash)[:5]
        log("info", f"  sample only_in_stash uuids: {sample}")
    if only_stashdb:
        sample = list(only_stashdb)[:5]
        log("info", f"  sample only_in_stashdb uuids: {sample}")
    if mon_needed:
        sample = list(mon_needed)[:5]
        log("info", f"  sample needing Whisparr monitored=True: {sample}")
    return summary


# ============================== main dispatch ==============================

def main() -> None:
    try:
        payload = read_plugin_input()
        args = payload.get("args") or {}
        hookctx = args.get("hookContext")

        cfg = validated_config()
        dry = cfg["dry_run"]

        if hookctx:
            log("info", f"hook fired: {hookctx.get('type')} id={hookctx.get('id')}")
            handle_hook(hookctx, cfg)
            emit_output({"output": "hook processed"})
            return

        mode = args.get("mode") or "report"
        log("info", f"task mode: {mode} (dry_run={dry})")

        if mode == "sync-all":
            result = handle_sync_all(cfg, dry=dry)
        elif mode == "monitor-whisparr":
            result = handle_monitor_whisparr_only(cfg, dry=dry)
        elif mode == "report":
            result = handle_report(cfg)
        else:
            raise RuntimeError(f"unknown mode: {mode}")

        emit_output({"output": result})
    except Exception as e:
        log("error", f"fatal: {e}")
        emit_output({"error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
