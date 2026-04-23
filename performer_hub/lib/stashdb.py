"""StashDB GraphQL client (stdlib urllib)."""
from typing import Optional

from .common import STASHDB_ENDPOINT_DEFAULT, http_json


class StashDBClient:
    def __init__(self, api_key: str, endpoint: str = STASHDB_ENDPOINT_DEFAULT):
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self._headers = {"ApiKey": api_key}

    def _query(self, query: str, variables: Optional[dict] = None) -> dict:
        data = http_json(
            self.endpoint,
            method="POST",
            headers=self._headers,
            json_body={"query": query, "variables": variables or {}},
        )
        if data.get("errors"):
            raise RuntimeError(f"StashDB GraphQL errors: {data['errors']}")
        return data.get("data") or {}

    # ---------------- favorites ----------------

    def get_favorite_performers(self) -> list:
        """Returns [{id, name, aliases}]. Uses is_favorite filter (slightly unreliable,
        see FavSync learnings — push is always idempotent so we don't rely on completeness)."""
        out: list = []
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
            block = data.get("queryPerformers") or {}
            items = block.get("performers") or []
            out.extend(items)
            if len(out) >= (block.get("count") or 0) or not items:
                break
            page += 1
            if page > 200:  # safety
                break
        return out

    def set_performer_favorite(self, performer_uuid: str, favorite: bool) -> str:
        """Returns 'set', 'already', or raises. 'already' means StashDB reported a duplicate-key
        constraint violation, which we treat as idempotent success."""
        try:
            self._query(
                "mutation($id: ID!, $fav: Boolean!) { favoritePerformer(id: $id, favorite: $fav) }",
                {"id": performer_uuid, "fav": favorite},
            )
            return "set"
        except RuntimeError as e:
            msg = str(e)
            if "duplicate key" in msg or "unique constraint" in msg:
                return "already"
            raise

    # ---------------- performer lookup ----------------

    def search_performer(self, name: str) -> list:
        data = self._query(
            "query($term: String!) { searchPerformer(term: $term) { id name disambiguation aliases } }",
            {"term": name},
        )
        return data.get("searchPerformer") or []

    def get_performer(self, performer_uuid: str) -> Optional[dict]:
        data = self._query(
            """query($id: ID!) {
                findPerformer(id: $id) {
                    id name aliases gender birth_date death_date country ethnicity
                    eye_color hair_color height career_start_year career_end_year
                }
            }""",
            {"id": performer_uuid},
        )
        return data.get("findPerformer")

    # ---------------- scene counts (for UI badges) ----------------

    def count_performer_scenes(self, performer_uuid: str) -> int:
        data = self._query(
            """query($input: SceneQueryInput!) {
                queryScenes(input: $input) { count }
            }""",
            {
                "input": {
                    "page": 1,
                    "per_page": 0,
                    "performers": {"value": [performer_uuid], "modifier": "INCLUDES"},
                }
            },
        )
        return ((data.get("queryScenes") or {}).get("count")) or 0

    def count_studio_scenes(self, studio_uuid: str, include_subsidiaries: bool = False) -> int:
        input_data: dict = {"page": 1, "per_page": 0}
        if include_subsidiaries:
            input_data["parentStudio"] = studio_uuid
        else:
            input_data["studios"] = {"value": [studio_uuid], "modifier": "INCLUDES"}
        data = self._query(
            """query($input: SceneQueryInput!) {
                queryScenes(input: $input) { count }
            }""",
            {"input": input_data},
        )
        return ((data.get("queryScenes") or {}).get("count")) or 0
