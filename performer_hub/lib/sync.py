"""Favorites sync — Stash ↔ StashDB ↔ Whisparr (Variant A: monitor-flag only).

Three operating modes plus a hook handler:
  - hook(performer_id)        : single-performer reactive sync (favorite toggle in Stash)
  - sync_all()                : union reconcile across all three systems
  - monitor_whisparr_bulk()   : Whisparr-only catchup, no Stash/StashDB writes
  - report()                  : dry-run diff report
"""
import json
from typing import Optional

from .common import STASHDB_ENDPOINT_MARKER, log
from .stash import StashClient, extract_stashdb_uuid
from .stashdb import StashDBClient
from .whisparr import WhisparrClient


# ============================== hook ==============================

def handle_hook(stash: StashClient, stashdb: StashDBClient, whisparr: Optional[WhisparrClient],
                performer_id: str, dry_run: bool) -> None:
    """React to Stash Performer.Update.Post: push favorite state to StashDB + Whisparr."""
    p = stash.find_performer(performer_id)
    if not p:
        log(f"hook: performer {performer_id} not found", "warning")
        return

    uuid = extract_stashdb_uuid(p)
    if not uuid:
        log(f"hook: {p.get('name')} has no StashDB UUID — skipping", "info")
        return

    favorite = bool(p.get("favorite"))
    log(f"hook: {p.get('name')} favorite={favorite} uuid={uuid}")

    # StashDB push (idempotent — duplicate-key treated as already-set)
    try:
        if dry_run:
            log(f"  [dry] would set StashDB favorite={favorite}")
        else:
            result = stashdb.set_performer_favorite(uuid, favorite)
            log(f"  StashDB favorite={favorite} ({result})")
    except Exception as e:
        log(f"  StashDB sync failed: {e}", "error")

    # Whisparr monitor-flag
    if not whisparr:
        return
    try:
        wp_perfs = whisparr.get_all_performers()
        match = next((x for x in wp_perfs if x.get("foreignId") == uuid), None)
        if not match:
            log(f"  Whisparr: no performer matches UUID {uuid}", "warning")
            return
        if match.get("monitored") == favorite:
            log(f"  Whisparr: monitored already {favorite}")
            return
        if dry_run:
            log(f"  [dry] would set Whisparr monitored={favorite}")
        else:
            whisparr.set_monitored(match, favorite)
            log(f"  Whisparr monitored={favorite}")
    except Exception as e:
        log(f"  Whisparr sync failed: {e}", "error")


# ============================== sync-all ==============================

def _build_union(stash_favs: list, sdb_favs: list) -> dict:
    """Return {uuid: {name, stash_id, in_stash, in_stashdb}} — union target state."""
    target: dict = {}
    for p in stash_favs:
        uuid = extract_stashdb_uuid(p)
        if not uuid:
            continue
        target[uuid] = {
            "name": p["name"],
            "stash_id": p["id"],
            "in_stash": True,
            "in_stashdb": False,
        }
    for s in sdb_favs:
        uuid = s["id"]
        if uuid in target:
            target[uuid]["in_stashdb"] = True
        else:
            target[uuid] = {
                "name": s["name"],
                "stash_id": None,
                "in_stash": False,
                "in_stashdb": True,
            }
    return target


def handle_sync_all(stash: StashClient, stashdb: StashDBClient, whisparr: Optional[WhisparrClient],
                    dry_run: bool) -> dict:
    log("fetching Stash favorites…")
    stash_favs = stash.find_favorite_performers()
    log(f"  stash favorites: {len(stash_favs)}")

    log("fetching StashDB favorites…")
    sdb_favs = stashdb.get_favorite_performers()
    log(f"  stashdb favorites: {len(sdb_favs)}")

    wp_by_uuid: dict = {}
    if whisparr:
        log("fetching Whisparr performers…")
        wp_perfs = whisparr.get_all_performers()
        wp_by_uuid = {x.get("foreignId"): x for x in wp_perfs if x.get("foreignId")}
        log(f"  whisparr performers: {len(wp_perfs)} ({len(wp_by_uuid)} with StashDB UUID)")

    target = _build_union(stash_favs, sdb_favs)
    log(f"  union target size: {len(target)}")

    summary = {
        "stash_favs": len(stash_favs),
        "stashdb_favs": len(sdb_favs),
        "union_target": len(target),
        "stash_updates": 0,
        "stashdb_updates": 0,
        "stashdb_already": 0,
        "whisparr_monitored_set": 0,
        "whisparr_missing_performer": 0,
        "errors": 0,
    }

    for uuid, info in target.items():
        name = info["name"]

        # 1. StashDB: idempotent push (queryPerformers is_favorite is unreliable, so always push)
        if dry_run:
            log(f"  [dry] STASHDB would ensure favorite for {name} ({uuid})")
        else:
            try:
                r = stashdb.set_performer_favorite(uuid, True)
                if r == "set":
                    summary["stashdb_updates"] += 1
                    log(f"  STASHDB favorited {name}")
                elif r == "already":
                    summary["stashdb_already"] += 1
            except Exception as e:
                summary["errors"] += 1
                log(f"  STASHDB update failed for {name}: {e}", "error")

        # 2. Stash: ensure favorite=True if performer exists locally
        if not info["in_stash"]:
            if dry_run:
                log(f"  [dry] STASH   would favorite {name} ({uuid})")
            else:
                try:
                    hit = stash.find_performer_by_stash_id("https://stashdb.org/graphql", uuid)
                    if hit:
                        stash.set_performer_favorite(hit["id"], True)
                        summary["stash_updates"] += 1
                        log(f"  STASH   favorited {name} (stash_id={hit['id']})")
                    else:
                        log(f"  STASH   uuid={uuid} ({name}) not in Stash — must be added manually", "warning")
                except Exception as e:
                    summary["errors"] += 1
                    log(f"  STASH   update failed for {name}: {e}", "error")

        # 3. Whisparr: ensure monitored=True if performer present in Whisparr catalog
        if not whisparr:
            continue
        wp = wp_by_uuid.get(uuid)
        if not wp:
            summary["whisparr_missing_performer"] += 1
            continue
        if wp.get("monitored"):
            continue
        if dry_run:
            log(f"  [dry] WHISPARR would set monitored=True for {name}")
        else:
            try:
                whisparr.set_monitored(wp, True)
                summary["whisparr_monitored_set"] += 1
                log(f"  WHISPARR monitored=True for {name}")
            except Exception as e:
                summary["errors"] += 1
                log(f"  WHISPARR update failed for {name}: {e}", "error")

    log(f"sync-all summary: {json.dumps(summary)}")
    return summary


# ============================== monitor-whisparr-only ==============================

def handle_monitor_whisparr_bulk(stash: StashClient, stashdb: StashDBClient,
                                  whisparr: WhisparrClient, dry_run: bool) -> dict:
    """Bulk-set monitored=True for every Stash ∪ StashDB favorite that exists in Whisparr.
    No Stash/StashDB writes."""
    log("monitor-whisparr-bulk: fetching favorites…")
    stash_favs = stash.find_favorite_performers()
    stash_uuids = {u for u in (extract_stashdb_uuid(p) for p in stash_favs) if u}

    sdb_favs = stashdb.get_favorite_performers()
    sdb_uuids = {s["id"] for s in sdb_favs}

    union = stash_uuids | sdb_uuids
    log(f"  union size: {len(union)} (stash={len(stash_uuids)}, stashdb={len(sdb_uuids)})")

    wp_perfs = whisparr.get_all_performers()
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
        name = wp.get("fullName") or uuid
        if dry_run:
            log(f"  [dry] would set monitored=True for {name} ({uuid})")
            continue
        try:
            whisparr.set_monitored(wp, True)
            summary["set_monitored"] += 1
            log(f"  monitored=True for {name}")
        except Exception as e:
            summary["errors"] += 1
            log(f"  failed for {name}: {e}", "error")

    log(f"monitor-whisparr summary: {json.dumps(summary)}")
    return summary


# ============================== report (dry-run diff) ==============================

def handle_report(stash: StashClient, stashdb: StashDBClient,
                  whisparr: Optional[WhisparrClient]) -> dict:
    log("REPORT mode — no writes")

    stash_favs = stash.find_favorite_performers()
    stash_uuids = {u for u in (extract_stashdb_uuid(p) for p in stash_favs) if u}

    sdb_favs = stashdb.get_favorite_performers()
    sdb_uuids = {s["id"] for s in sdb_favs}

    only_stash = stash_uuids - sdb_uuids
    only_stashdb = sdb_uuids - stash_uuids
    both = stash_uuids & sdb_uuids
    union = stash_uuids | sdb_uuids

    summary = {
        "stash_favs_total": len(stash_favs),
        "stash_favs_with_stashdb_uuid": len(stash_uuids),
        "stashdb_favs": len(sdb_uuids),
        "in_both": len(both),
        "only_in_stash": len(only_stash),
        "only_in_stashdb": len(only_stashdb),
    }

    if whisparr:
        wp_perfs = whisparr.get_all_performers()
        wp_monitored = [x for x in wp_perfs if x.get("monitored")]
        wp_mon_uuids = {x.get("foreignId") for x in wp_monitored if x.get("foreignId")}
        summary["whisparr_total"] = len(wp_perfs)
        summary["whisparr_monitored"] = len(wp_monitored)
        summary["whisparr_monitored_missing_for_favs"] = len(union - wp_mon_uuids)
        summary["whisparr_monitored_not_in_favs"] = len(wp_mon_uuids - union)

    log(f"REPORT summary:\n{json.dumps(summary, indent=2)}")

    if only_stash:
        log(f"  sample only_in_stash uuids: {list(only_stash)[:5]}")
    if only_stashdb:
        log(f"  sample only_in_stashdb uuids: {list(only_stashdb)[:5]}")

    return summary
