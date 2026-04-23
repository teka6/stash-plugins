"""Performer metadata enrichment from StashDB. Gap-fill only — never overwrites existing fields."""
import time
from typing import Optional

from .common import STASHDB_ENDPOINT_MARKER, log
from .stash import StashClient, extract_stashdb_uuid
from .stashdb import StashDBClient


# Map of remote (StashDB) field name → local (Stash) field name. Identical names omitted.
_REMOTE_TO_LOCAL = {
    "birth_date": "birthdate",
    "death_date": "death_date",
    "country": "country",
    "ethnicity": "ethnicity",
    "eye_color": "eye_color",
    "hair_color": "hair_color",
    "gender": "gender",
    "disambiguation": "disambiguation",
}


def build_gap_fill_update(local: dict, remote: dict, stashdb_uuid: str,
                          stashdb_endpoint: str) -> Optional[dict]:
    """Compose a PerformerUpdateInput that only fills empty fields. Returns None if no change."""
    update: dict = {"id": local["id"]}
    changed = False

    for remote_key, local_key in _REMOTE_TO_LOCAL.items():
        if not local.get(local_key) and remote.get(remote_key):
            update[local_key] = remote[remote_key]
            changed = True

    if not local.get("height_cm") and remote.get("height"):
        update["height_cm"] = remote["height"]
        changed = True

    start = remote.get("career_start_year")
    end = remote.get("career_end_year")
    if not local.get("career_length") and start:
        update["career_length"] = f"{start} - {end}" if end else f"{start} -"
        changed = True

    remote_aliases = remote.get("aliases") or []
    local_aliases = local.get("alias_list") or []
    if remote_aliases and not local_aliases:
        update["alias_list"] = remote_aliases
        changed = True

    # Attach StashDB UUID if missing — preserve any existing stash_ids on other endpoints.
    has_stashdb = any(STASHDB_ENDPOINT_MARKER in (sid.get("endpoint") or "")
                      for sid in (local.get("stash_ids") or []))
    if not has_stashdb and stashdb_uuid:
        existing = [
            {"endpoint": sid["endpoint"], "stash_id": sid["stash_id"]}
            for sid in (local.get("stash_ids") or [])
        ]
        existing.append({"endpoint": stashdb_endpoint, "stash_id": stashdb_uuid})
        update["stash_ids"] = existing
        changed = True

    return update if changed else None


def handle_enrich(stash: StashClient, stashdb: StashDBClient, stashdb_endpoint: str,
                  dry_run: bool, all_performers: bool, rate_limit: float) -> dict:
    if all_performers:
        performers = stash.find_favorite_performers()
        log(f"enriching all {len(performers)} favorites")
    else:
        performers = stash.find_favorites_missing_endpoint(stashdb_endpoint)
        log(f"found {len(performers)} favorites without StashDB UUID")

    summary = {
        "total": len(performers),
        "enriched": 0,
        "skipped_complete": 0,
        "not_found": 0,
        "errors": 0,
    }
    error_lines: list = []

    for i, performer in enumerate(performers, start=1):
        name = performer.get("name", "?")
        log(f"[{i}/{len(performers)}] {name}")

        # Resolve a StashDB UUID — either already linked, or by name search.
        stashdb_uuid = extract_stashdb_uuid(performer)
        if stashdb_uuid:
            try:
                remote = stashdb.get_performer(stashdb_uuid)
            except Exception as e:
                summary["errors"] += 1
                error_lines.append(f"{name}: lookup failed: {e}")
                continue
            if not remote:
                summary["not_found"] += 1
                log("  StashDB returned no record", "warning")
                continue
        else:
            try:
                matches = stashdb.search_performer(name)
            except Exception as e:
                summary["errors"] += 1
                error_lines.append(f"{name}: search failed: {e}")
                continue
            if not matches:
                summary["not_found"] += 1
                log("  not found on StashDB")
                continue
            stashdb_uuid = matches[0]["id"]
            try:
                remote = stashdb.get_performer(stashdb_uuid)
            except Exception as e:
                summary["errors"] += 1
                error_lines.append(f"{name}: lookup after match failed: {e}")
                continue

        # We need the FULL local performer record to compare gaps reliably (the
        # paginated query returns the full PERFORMER_FIELDS set already, but the
        # missing-endpoint query returns only id/name/favorite/stash_ids).
        if "birthdate" not in performer:
            full = stash.find_performer(performer["id"])
            if full:
                performer = full

        update = build_gap_fill_update(performer, remote, stashdb_uuid, stashdb_endpoint)

        if not update:
            summary["skipped_complete"] += 1
            log("  already complete")
            continue

        fields = [k for k in update if k != "id"]
        if dry_run:
            log(f"  [dry] would fill: {', '.join(fields)}")
            summary["enriched"] += 1
        else:
            try:
                stash.update_performer(update)
                summary["enriched"] += 1
                log(f"  filled: {', '.join(fields)}")
            except Exception as e:
                summary["errors"] += 1
                error_lines.append(f"{name}: update failed: {e}")
                continue

        if rate_limit > 0:
            time.sleep(rate_limit)

    log(
        f"enrich complete: {summary['enriched']} enriched, "
        f"{summary['skipped_complete']} skipped, {summary['not_found']} not found, "
        f"{summary['errors']} errors"
    )
    for line in error_lines:
        log(f"  ERROR: {line}", "error")
    return summary
