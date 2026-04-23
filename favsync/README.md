# FavSync — Stash ↔ StashDB ↔ Whisparr

Native Stash plugin. Keeps performer favorites in sync across all three systems. No DB access, no schema dependencies — pure HTTP/GraphQL.

## Capabilities

- **Hook `Performer.Update.Post`** — on every performer update, if the performer has a StashDB UUID, push the current `favorite` state to StashDB (`favoritePerformer` mutation) and set the matching Whisparr performer's `monitored` flag to match.
- **Task `Sync All Favorites`** — union reconcile. Everything favorited in Stash or StashDB gets favorited in both, and any matching Whisparr performer is set monitored.
- **Task `Report Diffs (dry-run)`** — no writes; reports what's out of sync.
- **Task `Monitor All Favs in Whisparr (bulk)`** — for every current favorite (Stash ∪ StashDB), sets `monitored=True` on matching Whisparr performer. Does not touch Stash/StashDB.

## Install

1. Copy this folder to your Stash plugin directory, e.g. `/config/plugins/nik/favsync/`.
2. In Stash UI: **Settings → Plugins → Reload** (or "Check for plugin updates" → "Installed").
3. **Settings → Plugins → FavSync → Settings:**
    - StashDB API Key
    - Whisparr URL (no trailing slash)
    - Whisparr API Key
    - Dry-Run (optional toggle)

## Use

- Toggle a favorite in Stash → hook fires automatically → syncs to StashDB + Whisparr.
- For existing favorites, run **Settings → Tasks → Plugin Tasks → FavSync → Monitor All Favs in Whisparr** once.
- To diagnose diffs, run **Report Diffs (dry-run)**.

## Behavior notes

- **Stash ↔ StashDB** matches on `stash_ids[].stash_id` where `endpoint` contains `stashdb.org`. Performers without a StashDB UUID are skipped (the hook logs them).
- **Whisparr match** is by `performer.foreignId == StashDB UUID`. If Whisparr doesn't know about the performer, bulk task reports it under `missing_in_whisparr`.
- **Union policy:** `Sync All` favorites a performer if ANY of Stash or StashDB has it favorited. There is no "remove favorite" reconcile — to unfavorite, toggle in Stash (the hook will propagate).
- **Dry-run mode:** set `dry_run=true` in settings to trace what would change without making any API writes.

## Architecture

- Stash self-auth via `SERVER_CONNECTION` env variable (session cookie) — the usual Stash plugin pattern.
- StashDB: `POST https://stashdb.org/graphql` with `ApiKey` header.
- Whisparr: REST `https://<host>/api/v3/performer[/{id}]` with `X-Api-Key` header.
- `PUT /api/v3/performer/{id}` with full object, only `monitored` field changed.

## Dependencies

- Python 3.9+ (stdlib only — `urllib`, `json`, `ssl`, `os`, `sys`).
- No external Python packages.

## Version

0.1.0
