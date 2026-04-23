#!/usr/bin/env python3
"""Performer Tools — Stash plugin for performer sync & enrichment.

Communicates via Stash plugin interface (JSON on stdin/stdout).
Reads StashDB config from Stash's configured stash boxes.
Whisparr config from plugin settings.
"""

import json
import sys
import requests
import time
import logging

log = logging.getLogger("performer_tools")


# ═══════════════════════════════════════════════════════════════
# Stash Plugin Interface
# ═══════════════════════════════════════════════════════════════

# Stash log protocol: \x01<level_code>\x02<message>\n on stderr
# Level codes: t=trace, d=debug, i=info, w=warning, e=error, p=progress
_LOG_LEVELS = {"trace": "t", "debug": "d", "info": "i", "warning": "w", "error": "e", "progress": "p"}


def read_plugin_input() -> dict:
    raw = sys.stdin.read()
    return json.loads(raw)


def write_plugin_output(output=None, error=None):
    result = {"output": output, "error": error}
    sys.stdout.write(json.dumps(result))


def log_to_stash(msg: str, level: str = "info"):
    """Write a log line using Stash's plugin log protocol."""
    code = _LOG_LEVELS.get(level, "i")
    for line in str(msg).split("\n"):
        if line.strip():
            sys.stderr.write(f"\x01{code}\x02{line}\n")
            sys.stderr.flush()


# ═══════════════════════════════════════════════════════════════
# GraphQL Clients (self-contained, no external dependencies)
# ═══════════════════════════════════════════════════════════════

class StashClient:
    """Minimal Stash GraphQL client using plugin server_connection."""

    def __init__(self, scheme: str, host: str, port: int, session_cookie=None, api_key: str = None):
        if host == "0.0.0.0":
            host = "127.0.0.1"
        self.base_url = f"{scheme}://{host}:{port}"
        self.graphql_url = f"{self.base_url}/graphql"
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"
        if api_key:
            self.session.headers["ApiKey"] = api_key
        # session_cookie from Stash is a dict with 'Value' key
        if session_cookie:
            cookie_val = session_cookie.get("Value", session_cookie) if isinstance(session_cookie, dict) else session_cookie
            self.session.cookies.set("session", cookie_val)
        # Fetch API key from Stash config to persist auth beyond session cookie
        if not api_key:
            try:
                data = self._query("{ configuration { general { apiKey } } }")
                fetched_key = data["configuration"]["general"]["apiKey"]
                if fetched_key:
                    self.session.headers["ApiKey"] = fetched_key
                    self.session.cookies.clear()
                    log_to_stash("Authenticated via Stash API key", "debug")
            except Exception as e:
                log_to_stash(f"Could not fetch API key from config: {e}", "warning")

    def _query(self, query: str, variables: dict = None) -> dict:
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = self.session.post(self.graphql_url, json=payload, timeout=600)
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data and data["errors"]:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data["data"]

    PERFORMER_FIELDS = """
        id name disambiguation gender favorite
        birthdate death_date country ethnicity
        eye_color hair_color height_cm
        career_length fake_tits
        tattoos piercings
        alias_list url
        image_path
        stash_ids { endpoint stash_id }
        tags { id name }
        scene_count
    """

    def get_performer_scene_count(self, performer_id: str, endpoint: str) -> int:
        """Count local scenes for a performer that have a stash_id on the given endpoint."""
        data = self._query(
            """query($filter: FindFilterType, $scene_filter: SceneFilterType) {
                findScenes(filter: $filter, scene_filter: $scene_filter) { count }
            }""",
            {
                "filter": {"page": 1, "per_page": 0},
                "scene_filter": {
                    "stash_id_endpoint": {"endpoint": endpoint, "stash_id": "", "modifier": "NOT_NULL"},
                    "performers": {"value": [performer_id], "excludes": [], "modifier": "INCLUDES_ALL"},
                },
            },
        )
        return data["findScenes"]["count"]

    def get_studio_scene_count(self, studio_id: str, endpoint: str, include_subsidiaries: bool = False) -> int:
        """Count local scenes for a studio that have a stash_id on the given endpoint."""
        studio_filter = {"value": [studio_id], "excludes": [], "modifier": "INCLUDES_ALL"}
        if include_subsidiaries:
            studio_filter["depth"] = -1
        data = self._query(
            """query($filter: FindFilterType, $scene_filter: SceneFilterType) {
                findScenes(filter: $filter, scene_filter: $scene_filter) { count }
            }""",
            {
                "filter": {"page": 1, "per_page": 0},
                "scene_filter": {
                    "stash_id_endpoint": {"endpoint": endpoint, "stash_id": "", "modifier": "NOT_NULL"},
                    "studios": studio_filter,
                },
            },
        )
        return data["findScenes"]["count"]

    def get_stats(self) -> dict:
        data = self._query("{ stats { scene_count performer_count studio_count group_count tag_count } }")
        return data["stats"]

    def get_stashbox_config(self) -> list:
        data = self._query("{ configuration { general { stashBoxes { endpoint name } } } }")
        return data["configuration"]["general"]["stashBoxes"]

    def get_favorite_performers(self) -> list:
        performers = []
        page = 1
        while True:
            data = self._query(
                """query($page: Int!, $per_page: Int!) {
                    findPerformers(
                        performer_filter: { filter_favorites: true }
                        filter: { page: $page, per_page: $per_page, sort: "name", direction: ASC }
                    ) { count performers { %s } }
                }""" % self.PERFORMER_FIELDS,
                {"page": page, "per_page": 100},
            )
            result = data["findPerformers"]
            performers.extend(result["performers"])
            if len(performers) >= result["count"]:
                break
            page += 1
        return performers

    def get_performer(self, performer_id: str) -> dict:
        data = self._query(
            "query($id: ID!) { findPerformer(id: $id) { %s } }" % self.PERFORMER_FIELDS,
            {"id": performer_id},
        )
        return data["findPerformer"]

    def set_performer_favorite(self, performer_id: str, favorite: bool):
        self._query(
            "mutation($id: ID!, $fav: Boolean!) { performerUpdate(input: { id: $id, favorite: $fav }) { id } }",
            {"id": performer_id, "fav": favorite},
        )

    def update_performer(self, input_data: dict) -> dict:
        data = self._query(
            "mutation($input: PerformerUpdateInput!) { performerUpdate(input: $input) { id name } }",
            {"input": input_data},
        )
        return data["performerUpdate"]

    def find_performers_by_stash_id(self, endpoint: str) -> list:
        performers = []
        page = 1
        while True:
            data = self._query(
                """query($page: Int!, $per_page: Int!, $endpoint: String!) {
                    findPerformers(
                        performer_filter: { stash_id_endpoint: { endpoint: $endpoint, modifier: NOT_NULL } }
                        filter: { page: $page, per_page: $per_page }
                    ) { count performers { id name stash_ids { endpoint stash_id } favorite } }
                }""",
                {"page": page, "per_page": 100, "endpoint": endpoint},
            )
            result = data["findPerformers"]
            performers.extend(result["performers"])
            if len(performers) >= result["count"]:
                break
            page += 1
        return performers

    def find_performers_missing_stashdb(self, stashdb_endpoint: str) -> list:
        data = self._query(
            """query {
                findPerformers(
                    performer_filter: {
                        stash_id_endpoint: { endpoint: "%s", modifier: IS_NULL }
                        filter_favorites: true
                    }
                    filter: { per_page: -1, sort: "name", direction: ASC }
                ) { count performers { id name stash_ids { endpoint stash_id } favorite } }
            }""" % stashdb_endpoint
        )
        return data["findPerformers"]["performers"]


class StashDBClient:
    """Minimal StashDB GraphQL client."""

    def __init__(self, endpoint: str, api_key: str):
        self.endpoint = endpoint.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "ApiKey": api_key})

    def _query(self, query: str, variables: dict = None) -> dict:
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = self.session.post(self.endpoint, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data and data["errors"]:
            raise RuntimeError(f"StashDB errors: {data['errors']}")
        return data["data"]

    def get_favorite_performers(self) -> list:
        performers = []
        page = 1
        while True:
            data = self._query(
                """query($page: Int!, $per_page: Int!) {
                    queryPerformers(input: { is_favorite: true, page: $page, per_page: $per_page, sort: NAME, direction: ASC }) {
                        count performers { id name aliases }
                    }
                }""",
                {"page": page, "per_page": 100},
            )
            result = data["queryPerformers"]
            performers.extend(result["performers"])
            if len(performers) >= result["count"]:
                break
            page += 1
        return performers

    def set_favorite_performer(self, performer_id: str, favorite: bool = True):
        self._query(
            "mutation($id: ID!, $favorite: Boolean!) { favoritePerformer(id: $id, favorite: $favorite) }",
            {"id": performer_id, "favorite": favorite},
        )

    def get_performer_scene_count(self, stash_id: str) -> int:
        """Get scene count for a performer on StashDB."""
        data = self._query(
            """query($input: SceneQueryInput!) {
                queryScenes(input: $input) { count }
            }""",
            {"input": {"page": 1, "per_page": 0, "sort": "DATE", "direction": "DESC",
                       "performers": {"value": [stash_id], "modifier": "INCLUDES"},
                       "studios": {"value": [], "modifier": "INCLUDES"}}},
        )
        return data["queryScenes"]["count"]

    def get_studio_scene_count(self, stash_id: str, include_subsidiaries: bool = False) -> int:
        """Get scene count for a studio on StashDB."""
        input_data = {"page": 1, "per_page": 0, "sort": "DATE", "direction": "DESC",
                      "performers": {"value": [], "modifier": "INCLUDES"}}
        if include_subsidiaries:
            input_data["parentStudio"] = stash_id
        else:
            input_data["studios"] = {"value": [stash_id], "modifier": "INCLUDES"}
        data = self._query(
            """query($input: SceneQueryInput!) {
                queryScenes(input: $input) { count }
            }""",
            {"input": input_data},
        )
        return data["queryScenes"]["count"]

    def search_performer(self, name: str) -> list:
        data = self._query(
            "query($term: String!) { searchPerformer(term: $term) { id name disambiguation aliases } }",
            {"term": name},
        )
        return data["searchPerformer"]

    def get_performer(self, performer_id: str) -> dict:
        data = self._query(
            """query($id: ID!) {
                findPerformer(id: $id) {
                    id name aliases gender birth_date death_date country ethnicity
                    eye_color hair_color height career_start_year career_end_year
                    tattoos { location description }
                    piercings { location description }
                }
            }""",
            {"id": performer_id},
        )
        return data["findPerformer"]


class WhisparrClient:
    """Minimal Whisparr REST client."""

    def __init__(self, url: str, api_key: str):
        self.base = f"{url.rstrip('/')}/api/v3"
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "X-Api-Key": api_key})

    def _request(self, method: str, endpoint: str, **kwargs):
        resp = self.session.request(method, f"{self.base}{endpoint}", timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    def get_import_lists(self) -> list:
        return self._request("GET", "/importlist").json()

    def create_import_list(self, performer_name: str, tpdb_performer_id: str,
                           root_folder_path: str, quality_profile_id: int) -> dict:
        payload = {
            "enableAutomaticAdd": True,
            "searchForMissingEpisodes": True,
            "shouldMonitor": "specificEpisode",
            "siteMonitorType": "all",
            "monitorNewItems": "all",
            "rootFolderPath": root_folder_path,
            "qualityProfileId": quality_profile_id,
            "listType": "advanced",
            "listOrder": 5,
            "minRefreshInterval": "06:00:00",
            "name": f"{performer_name} - {tpdb_performer_id}",
            "implementation": "TPDbPerformer",
            "configContract": "TPDbPerformerSettings",
            "fields": [
                {"name": "performerId", "value": tpdb_performer_id},
                {"name": "excludedSiteIds", "value": []},
            ],
        }
        return self._request("POST", "/importlist", json=payload).json()


# ═══════════════════════════════════════════════════════════════
# Task: Status
# ═══════════════════════════════════════════════════════════════

def task_status(stash: StashClient, stashdb: StashDBClient, whisparr: WhisparrClient):
    stats = stash.get_stats()
    stash_favs = stash.get_favorite_performers()

    with_stashdb = sum(1 for p in stash_favs if any("stashdb" in s.get("endpoint", "") for s in p.get("stash_ids", [])))
    with_tpdb = sum(1 for p in stash_favs if any("theporndb" in s.get("endpoint", "") for s in p.get("stash_ids", [])))

    lines = [
        "=" * 60,
        "PERFORMER STATUS",
        "=" * 60,
        f"Stash:    {stats['performer_count']:,} total | {len(stash_favs):,} favorites | "
        f"{with_stashdb:,} w/ StashDB ID | {with_tpdb:,} w/ TPDB ID",
    ]

    if stashdb:
        try:
            db_favs = stashdb.get_favorite_performers()
            lines.append(f"StashDB:  {len(db_favs):,} favorites (connected)")
        except Exception as e:
            lines.append(f"StashDB:  error ({e})")
    else:
        lines.append("StashDB:  not configured")

    if whisparr:
        try:
            lists = whisparr.get_import_lists()
            lines.append(f"Whisparr: {len(lists):,} import lists (connected)")
        except Exception as e:
            lines.append(f"Whisparr: error ({e})")
    else:
        lines.append("Whisparr: not configured (add URL/key in plugin settings)")

    output = "\n".join(lines)
    log_to_stash(output)
    return output


# ═══════════════════════════════════════════════════════════════
# Task: Sync
# ═══════════════════════════════════════════════════════════════

def task_sync(stash: StashClient, stashdb: StashDBClient, whisparr: WhisparrClient,
              stashdb_endpoint: str, dry_run: bool, stashdb_only: bool, whisparr_only: bool,
              rate_limit: float, whisparr_cfg: dict):
    report = {"stashdb_pushed": 0, "stashdb_pulled": 0, "whisparr_created": 0, "errors": []}
    prefix = "[DRY-RUN] " if dry_run else ""

    stash_favs = stash.get_favorite_performers()
    log_to_stash(f"Stash favorites: {len(stash_favs)}")

    # ── StashDB sync ──
    if not whisparr_only and stashdb:
        local_by_stashdb_id = {}
        for p in stash_favs:
            for sid in p.get("stash_ids", []):
                if "stashdb" in sid.get("endpoint", ""):
                    local_by_stashdb_id[sid["stash_id"]] = p
                    break

        log_to_stash(f"Stash favorites with StashDB ID: {len(local_by_stashdb_id)}")

        try:
            stashdb_favs = stashdb.get_favorite_performers()
        except Exception as e:
            report["errors"].append(f"StashDB favorites fetch failed: {e}")
            stashdb_favs = []

        stashdb_fav_ids = {p["id"] for p in stashdb_favs}
        log_to_stash(f"StashDB favorites: {len(stashdb_fav_ids)}")

        # Push: local favorites → StashDB
        to_push = [(sid, p["name"]) for sid, p in local_by_stashdb_id.items() if sid not in stashdb_fav_ids]
        if to_push:
            log_to_stash(f"{prefix}Pushing {len(to_push)} favorites to StashDB")
        for stashdb_id, name in to_push:
            if dry_run:
                log_to_stash(f"  {prefix}Would push: {name}")
            else:
                try:
                    stashdb.set_favorite_performer(stashdb_id, True)
                    log_to_stash(f"  Pushed: {name}")
                    if rate_limit > 0:
                        time.sleep(rate_limit)
                except Exception as e:
                    report["errors"].append(f"Push {name}: {e}")
                    continue
            report["stashdb_pushed"] += 1

        # Pull: StashDB favorites → Stash
        all_local = stash.find_performers_by_stash_id(stashdb_endpoint)
        for p in all_local:
            for sid in p.get("stash_ids", []):
                if "stashdb" in sid.get("endpoint", "") and sid["stash_id"] in stashdb_fav_ids and not p.get("favorite"):
                    if dry_run:
                        log_to_stash(f"  {prefix}Would pull: {p['name']}")
                    else:
                        try:
                            stash.set_performer_favorite(p["id"], True)
                            log_to_stash(f"  Pulled: {p['name']}")
                        except Exception as e:
                            report["errors"].append(f"Pull {p['name']}: {e}")
                            continue
                    report["stashdb_pulled"] += 1

    # ── Whisparr sync ──
    if not stashdb_only and whisparr:
        root_folder = whisparr_cfg.get("root_folder", "")
        quality_profile = int(whisparr_cfg.get("quality_profile", 8))

        try:
            existing_lists = whisparr.get_import_lists()
        except Exception as e:
            report["errors"].append(f"Whisparr lists fetch failed: {e}")
            existing_lists = []

        whisparr_by_name = {}
        for lst in existing_lists:
            list_name = lst.get("name", "")
            pname = list_name.rsplit(" - ", 1)[0].strip().lower() if " - " in list_name else list_name.lower()
            whisparr_by_name[pname] = lst

        for p in stash_favs:
            if p["name"].lower() not in whisparr_by_name:
                tpdb_id = None
                for sid in p.get("stash_ids", []):
                    if "theporndb" in sid.get("endpoint", ""):
                        tpdb_id = sid["stash_id"]
                        break
                if tpdb_id:
                    if dry_run:
                        log_to_stash(f"  {prefix}Would create list: {p['name']}")
                    else:
                        try:
                            whisparr.create_import_list(p["name"], tpdb_id, root_folder, quality_profile)
                            log_to_stash(f"  Created list: {p['name']}")
                        except Exception as e:
                            report["errors"].append(f"Create list {p['name']}: {e}")
                            continue
                    report["whisparr_created"] += 1

    # Report
    summary = (
        f"\n{'='*60}\n{prefix}SYNC REPORT\n{'='*60}\n"
        f"StashDB pushed:   {report['stashdb_pushed']}\n"
        f"StashDB pulled:   {report['stashdb_pulled']}\n"
        f"Whisparr created: {report['whisparr_created']}\n"
        f"Errors:           {len(report['errors'])}"
    )
    if report["errors"]:
        summary += "\n" + "\n".join(f"  ERROR: {e}" for e in report["errors"])
    log_to_stash(summary)
    return summary


# ═══════════════════════════════════════════════════════════════
# Task: Enrich
# ═══════════════════════════════════════════════════════════════

def build_gap_fill(local: dict, remote: dict, stashdb_id: str, stashdb_endpoint: str) -> dict | None:
    update = {"id": local["id"]}
    changed = False

    field_map = {
        "birth_date": "birthdate", "death_date": "death_date", "country": "country",
        "ethnicity": "ethnicity", "eye_color": "eye_color", "hair_color": "hair_color",
        "gender": "gender", "disambiguation": "disambiguation",
    }
    for remote_key, local_key in field_map.items():
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

    remote_aliases = remote.get("aliases", [])
    local_aliases = local.get("alias_list", []) or []
    if remote_aliases and not local_aliases:
        update["alias_list"] = remote_aliases
        changed = True

    has_stashdb = any("stashdb" in sid.get("endpoint", "") for sid in local.get("stash_ids", []))
    if not has_stashdb and stashdb_id:
        existing_ids = [{"endpoint": sid["endpoint"], "stash_id": sid["stash_id"]} for sid in local.get("stash_ids", [])]
        existing_ids.append({"endpoint": stashdb_endpoint, "stash_id": stashdb_id})
        update["stash_ids"] = existing_ids
        changed = True

    return update if changed else None


def task_enrich(stash: StashClient, stashdb: StashDBClient, stashdb_endpoint: str,
                dry_run: bool, all_performers: bool, rate_limit: float):
    if not stashdb:
        log_to_stash("StashDB not configured", "error")
        return "StashDB not configured"

    if all_performers:
        performers = stash.get_favorite_performers()
        log_to_stash(f"Enriching all {len(performers)} favorites")
    else:
        performers = stash.find_performers_missing_stashdb(stashdb_endpoint)
        log_to_stash(f"Found {len(performers)} performers without StashDB ID")

    enriched, skipped, not_found, errors = 0, 0, 0, []

    for i, performer in enumerate(performers):
        name = performer.get("name", "?")
        log_to_stash(f"[{i+1}/{len(performers)}] Processing {name}...")

        stashdb_id = None
        for sid in performer.get("stash_ids", []):
            if "stashdb" in sid.get("endpoint", ""):
                stashdb_id = sid["stash_id"]
                break

        if stashdb_id:
            try:
                stashdb_data = stashdb.get_performer(stashdb_id)
            except Exception as e:
                errors.append(f"{name}: lookup failed: {e}")
                continue
        else:
            try:
                matches = stashdb.search_performer(name)
            except Exception as e:
                errors.append(f"{name}: search failed: {e}")
                continue
            if not matches:
                log_to_stash(f"  Not found on StashDB")
                not_found += 1
                continue
            stashdb_data = matches[0]
            stashdb_id = stashdb_data["id"]

        # Need full performer data for gap-fill
        if "id" in performer and len(performer.keys()) < 10:
            performer = stash.get_performer(performer["id"])

        update = build_gap_fill(performer, stashdb_data, stashdb_id, stashdb_endpoint)

        if not update:
            log_to_stash(f"  Already complete, skipping")
            skipped += 1
            continue

        fields_filled = [k for k in update if k != "id"]
        if dry_run:
            log_to_stash(f"  [DRY-RUN] Would fill: {', '.join(fields_filled)}")
        else:
            try:
                stash.update_performer(update)
                log_to_stash(f"  Filled: {', '.join(fields_filled)}")
            except Exception as e:
                errors.append(f"{name}: update failed: {e}")
                continue

        enriched += 1
        if rate_limit > 0:
            time.sleep(rate_limit)

    summary = f"Enrich complete: {enriched} enriched, {skipped} skipped, {not_found} not found, {len(errors)} errors"
    if errors:
        summary += "\n" + "\n".join(f"  ERROR: {e}" for e in errors)
    log_to_stash(summary)
    return summary


# ═══════════════════════════════════════════════════════════════
# Task: Stashbox Scene Count
# ═══════════════════════════════════════════════════════════════

def task_stashbox_performer_scene_count(stash: StashClient, stashdb: StashDBClient,
                                         endpoint: str, api_key: str, stash_id: str,
                                         include_subsidiaries: bool = False):
    """Get scene count from StashDB and local Stash for a performer, output for UI badge."""
    # Use provided api_key/endpoint (passed from JS which reads stash box config)
    client = StashDBClient(endpoint=endpoint, api_key=api_key) if endpoint and api_key else stashdb
    if not client:
        log_to_stash(f"{stash_id}: error (no StashDB client)", "error")
        return

    try:
        stashbox_count = client.get_performer_scene_count(stash_id)
    except Exception as e:
        log_to_stash(f"{stash_id}: error ({e})", "error")
        return

    # Find local performer by stash_id to get their local ID
    try:
        local_performers = stash.find_performers_by_stash_id(endpoint)
        local_id = None
        for p in local_performers:
            for sid in p.get("stash_ids", []):
                if sid.get("stash_id") == stash_id:
                    local_id = p["id"]
                    break
            if local_id:
                break
        local_count = stash.get_performer_scene_count(local_id, endpoint) if local_id else 0
    except Exception:
        local_count = 0

    # Output in exact format the JS expects: "{stash_id}: {local}/{stashbox}"
    log_to_stash(f"{stash_id}: {local_count}/{stashbox_count}")


def task_stashbox_studio_scene_count(stash: StashClient, stashdb: StashDBClient,
                                      endpoint: str, api_key: str, stash_id: str,
                                      include_subsidiaries: bool = False):
    """Get scene count from StashDB and local Stash for a studio, output for UI badge."""
    client = StashDBClient(endpoint=endpoint, api_key=api_key) if endpoint and api_key else stashdb
    if not client:
        log_to_stash(f"{stash_id}: error (no StashDB client)", "error")
        return

    try:
        stashbox_count = client.get_studio_scene_count(stash_id, include_subsidiaries)
    except Exception as e:
        log_to_stash(f"{stash_id}: error ({e})", "error")
        return

    # Find local studio by stash_id
    try:
        data = stash._query(
            """query($endpoint: String!) {
                findStudios(studio_filter: { stash_id_endpoint: { endpoint: $endpoint, modifier: NOT_NULL } },
                            filter: { per_page: -1 }) {
                    studios { id stash_ids { endpoint stash_id } }
                }
            }""", {"endpoint": endpoint})
        local_id = None
        for s in data["findStudios"]["studios"]:
            for sid in s.get("stash_ids", []):
                if sid.get("stash_id") == stash_id:
                    local_id = s["id"]
                    break
            if local_id:
                break
        local_count = stash.get_studio_scene_count(local_id, endpoint, include_subsidiaries) if local_id else 0
    except Exception:
        local_count = 0

    log_to_stash(f"{stash_id}: {local_count}/{stashbox_count}")


# ═══════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════

def main():
    plugin_input = read_plugin_input()

    server = plugin_input.get("server_connection", {})
    args = plugin_input.get("args", {})
    mode = args.get("mode", "status")

    # Log raw server_connection keys for debugging
    log_to_stash(f"server_connection keys: {list(server.keys())}", "debug")

    # Build Stash client from server_connection
    # Keys can be camelCase or snake_case depending on Stash version
    stash = StashClient(
        scheme=server.get("Scheme", server.get("scheme", "http")),
        host=server.get("Host", server.get("host", "localhost")),
        port=server.get("Port", server.get("port", 9999)),
        session_cookie=server.get("SessionCookie", server.get("session_cookie")),
    )

    # Discover StashDB endpoint from Stash's configured stash boxes
    stashdb = None
    stashdb_endpoint = ""
    try:
        stash_boxes = stash.get_stashbox_config()
        for box in stash_boxes:
            if "stashdb" in box.get("endpoint", "").lower():
                stashdb_endpoint = box["endpoint"]
                break
    except Exception as e:
        log_to_stash(f"Could not read stash box config: {e}", "warning")

    # Read plugin settings via GraphQL
    whisparr = None
    whisparr_cfg = {}
    rate_limit = 2.0
    pt_config = {}

    try:
        config_data = stash._query("""{ configuration { plugins } }""")
        plugin_configs = config_data.get("configuration", {}).get("plugins", {})
        pt_config = plugin_configs.get("performer_tools", {})

        # StashDB client from plugin settings + discovered endpoint
        stashdb_api_key = pt_config.get("stashdbApiKey", "")
        if stashdb_endpoint and stashdb_api_key:
            stashdb = StashDBClient(endpoint=stashdb_endpoint, api_key=stashdb_api_key)
            log_to_stash(f"Using StashDB: {stashdb_endpoint}")

        # Whisparr client from plugin settings
        w_url = pt_config.get("whisparrUrl", "")
        w_key = pt_config.get("whisparrApiKey", "")
        if w_url and w_key:
            whisparr = WhisparrClient(url=w_url, api_key=w_key)
            whisparr_cfg = {
                "root_folder": pt_config.get("whisparrRootFolder", ""),
                "quality_profile": pt_config.get("whisparrQualityProfile", 8),
            }
            log_to_stash(f"Using Whisparr: {w_url}")

        rl = pt_config.get("rateLimitSeconds")
        if rl is not None:
            rate_limit = float(rl)
    except Exception as e:
        log_to_stash(f"Could not read plugin settings: {e}", "warning")

    # dryRun from plugin settings (toggle in UI), default true for safety
    dry_run = pt_config.get("dryRun", True)
    if isinstance(dry_run, str):
        dry_run = dry_run.lower() == "true"
    stashdb_only = args.get("stashdbOnly", "false").lower() == "true"
    whisparr_only = args.get("whisparrOnly", "false").lower() == "true"
    all_performers = args.get("allPerformers", "false").lower() == "true"

    # Scene count tasks get endpoint/api_key/stash_id from JS caller
    sc_endpoint = args.get("endpoint", "")
    sc_api_key = args.get("api_key", "")
    sc_stash_id = args.get("stash_id", "")
    include_subsidiaries = False
    try:
        config_data = stash._query("""{ configuration { plugins } }""")
        pt_config = config_data.get("configuration", {}).get("plugins", {}).get("performer_tools", {})
        include_subsidiaries = pt_config.get("includeSubsidiaryStudios", False)
    except Exception:
        pass

    try:
        if mode == "status":
            result = task_status(stash, stashdb, whisparr)
        elif mode == "sync":
            result = task_sync(stash, stashdb, whisparr, stashdb_endpoint, dry_run, stashdb_only, whisparr_only, rate_limit, whisparr_cfg)
        elif mode == "enrich":
            result = task_enrich(stash, stashdb, stashdb_endpoint, dry_run, all_performers, rate_limit)
        elif mode == "stashbox_performer_scene_count":
            task_stashbox_performer_scene_count(stash, stashdb, sc_endpoint, sc_api_key, sc_stash_id)
            result = "ok"
        elif mode == "stashbox_studio_scene_count":
            task_stashbox_studio_scene_count(stash, stashdb, sc_endpoint, sc_api_key, sc_stash_id, include_subsidiaries)
            result = "ok"
        else:
            result = f"Unknown mode: {mode}"

        write_plugin_output(output=result)
    except Exception as e:
        log_to_stash(f"FATAL: {e}", "error")
        write_plugin_output(error=str(e))


if __name__ == "__main__":
    main()
