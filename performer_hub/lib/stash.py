"""Stash GraphQL client — reads server_connection from plugin-input stdin."""
from typing import Optional

from .common import PLUGIN_ID, STASHDB_ENDPOINT_MARKER, http_json, log


PERFORMER_FIELDS = """
    id name disambiguation gender favorite
    birthdate death_date country ethnicity
    eye_color hair_color height_cm
    career_length
    alias_list
    stash_ids { endpoint stash_id }
"""


class StashClient:
    """Minimal Stash GraphQL client. Authenticates via session cookie or API key
    fetched from Stash configuration."""

    def __init__(self, server_connection: dict):
        scheme = server_connection.get("Scheme") or server_connection.get("scheme") or "http"
        host = server_connection.get("Host") or server_connection.get("host") or "localhost"
        if host in ("0.0.0.0", ""):
            host = "127.0.0.1"
        port = server_connection.get("Port") or server_connection.get("port") or 9999

        self.base_url = f"{scheme}://{host}:{port}"
        self.graphql_url = f"{self.base_url}/graphql"

        cookie = server_connection.get("SessionCookie") or server_connection.get("session_cookie") or {}
        self._headers: dict = {}
        if cookie and cookie.get("Name"):
            self._headers["Cookie"] = f"{cookie['Name']}={cookie['Value']}"

        # Upgrade auth to API key so writes persist beyond the short-lived plugin session cookie.
        try:
            data = self._raw_query("{ configuration { general { apiKey } } }")
            api_key = ((data.get("configuration") or {}).get("general") or {}).get("apiKey")
            if api_key:
                self._headers = {"ApiKey": api_key}
                log("authenticated via Stash API key", "debug")
        except Exception as e:
            log(f"could not fetch Stash API key: {e}", "warning")

    def _raw_query(self, query: str, variables: Optional[dict] = None) -> dict:
        data = http_json(
            self.graphql_url,
            method="POST",
            headers=self._headers,
            json_body={"query": query, "variables": variables or {}},
        )
        if data.get("errors"):
            raise RuntimeError(f"Stash GraphQL errors: {data['errors']}")
        return data.get("data") or {}

    def query(self, query: str, variables: Optional[dict] = None) -> dict:
        return self._raw_query(query, variables)

    # ---------------- config ----------------

    def get_plugin_settings(self) -> dict:
        data = self._raw_query("{ configuration { plugins } }")
        plugins = (data.get("configuration") or {}).get("plugins") or {}
        return plugins.get(PLUGIN_ID) or {}

    def get_stash_boxes(self) -> list:
        data = self._raw_query("{ configuration { general { stashBoxes { endpoint name api_key } } } }")
        return ((data.get("configuration") or {}).get("general") or {}).get("stashBoxes") or []

    def get_stats(self) -> dict:
        data = self._raw_query("{ stats { scene_count performer_count studio_count tag_count } }")
        return data.get("stats") or {}

    # ---------------- performer queries ----------------

    def find_performer(self, performer_id: str) -> Optional[dict]:
        data = self._raw_query(
            "query($id: ID!) { findPerformer(id: $id) { %s } }" % PERFORMER_FIELDS,
            {"id": performer_id},
        )
        return data.get("findPerformer")

    def find_favorite_performers(self) -> list:
        """Return all Stash performers with favorite=true, paginated."""
        out: list = []
        page = 1
        while True:
            data = self._raw_query(
                """query($page: Int!, $per_page: Int!) {
                    findPerformers(
                        performer_filter: { filter_favorites: true }
                        filter: { page: $page, per_page: $per_page, sort: "name", direction: ASC }
                    ) { count performers { %s } }
                }""" % PERFORMER_FIELDS,
                {"page": page, "per_page": 100},
            )
            block = data.get("findPerformers") or {}
            items = block.get("performers") or []
            out.extend(items)
            if len(out) >= (block.get("count") or 0) or not items:
                break
            page += 1
        return out

    def find_performers_linked_to_endpoint(self, endpoint: str) -> list:
        """Return all performers that have a stash_id on the given endpoint."""
        out: list = []
        page = 1
        while True:
            data = self._raw_query(
                """query($page: Int!, $per_page: Int!, $endpoint: String!) {
                    findPerformers(
                        performer_filter: { stash_id_endpoint: { endpoint: $endpoint, modifier: NOT_NULL } }
                        filter: { page: $page, per_page: $per_page }
                    ) { count performers { id name favorite stash_ids { endpoint stash_id } } }
                }""",
                {"page": page, "per_page": 100, "endpoint": endpoint},
            )
            block = data.get("findPerformers") or {}
            items = block.get("performers") or []
            out.extend(items)
            if len(out) >= (block.get("count") or 0) or not items:
                break
            page += 1
        return out

    def find_favorites_missing_endpoint(self, endpoint: str) -> list:
        """Favorites that do NOT have a stash_id on the given endpoint."""
        data = self._raw_query(
            """query($endpoint: String!) {
                findPerformers(
                    performer_filter: {
                        stash_id_endpoint: { endpoint: $endpoint, modifier: IS_NULL }
                        filter_favorites: true
                    }
                    filter: { per_page: -1, sort: "name", direction: ASC }
                ) { count performers { id name favorite stash_ids { endpoint stash_id } } }
            }""",
            {"endpoint": endpoint},
        )
        return (data.get("findPerformers") or {}).get("performers") or []

    def find_performer_by_stash_id(self, endpoint: str, stash_id: str) -> Optional[dict]:
        data = self._raw_query(
            """query($endpoint: String!, $uuid: String!) {
                findPerformers(
                    performer_filter: {
                        stash_id_endpoint: { endpoint: $endpoint, stash_id: $uuid, modifier: EQUALS }
                    }
                    filter: { per_page: 1 }
                ) { performers { id name favorite } }
            }""",
            {"endpoint": endpoint, "uuid": stash_id},
        )
        hits = (data.get("findPerformers") or {}).get("performers") or []
        return hits[0] if hits else None

    # ---------------- performer mutations ----------------

    def set_performer_favorite(self, performer_id: str, favorite: bool) -> None:
        self._raw_query(
            "mutation($id: ID!, $fav: Boolean!) { performerUpdate(input: { id: $id, favorite: $fav }) { id favorite } }",
            {"id": performer_id, "fav": favorite},
        )

    def update_performer(self, input_data: dict) -> dict:
        data = self._raw_query(
            "mutation($input: PerformerUpdateInput!) { performerUpdate(input: $input) { id name } }",
            {"input": input_data},
        )
        return data.get("performerUpdate") or {}

    # ---------------- scene counts (for UI badges) ----------------

    def count_performer_scenes_on_endpoint(self, performer_id: str, endpoint: str) -> int:
        data = self._raw_query(
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
        return ((data.get("findScenes") or {}).get("count")) or 0

    def count_studio_scenes_on_endpoint(
        self, studio_id: str, endpoint: str, include_subsidiaries: bool = False
    ) -> int:
        studio_filter: dict = {"value": [studio_id], "excludes": [], "modifier": "INCLUDES_ALL"}
        if include_subsidiaries:
            studio_filter["depth"] = -1
        data = self._raw_query(
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
        return ((data.get("findScenes") or {}).get("count")) or 0

    def find_studios_linked_to_endpoint(self, endpoint: str) -> list:
        data = self._raw_query(
            """query($endpoint: String!) {
                findStudios(
                    studio_filter: { stash_id_endpoint: { endpoint: $endpoint, modifier: NOT_NULL } }
                    filter: { per_page: -1 }
                ) { studios { id stash_ids { endpoint stash_id } } }
            }""",
            {"endpoint": endpoint},
        )
        return (data.get("findStudios") or {}).get("studios") or []


def extract_stashdb_uuid(performer: dict) -> Optional[str]:
    """Pull StashDB UUID out of a Stash performer's stash_ids list."""
    for sid in performer.get("stash_ids") or []:
        if STASHDB_ENDPOINT_MARKER in (sid.get("endpoint") or ""):
            return sid.get("stash_id")
    return None
