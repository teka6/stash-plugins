# Performer Hub

Native [Stash](https://github.com/stashapp/stash) plugin that unifies performer-favorites sync across **Stash**, **StashDB**, and **Whisparr**, gap-fills StashDB metadata onto local performers, and shows scene-count badges in the UI.

Successor to the separate `favsync` and `performer_tools` plugins. Zero external dependencies — Python 3 stdlib + vanilla JavaScript.

## Features

### Hook

| Trigger | Behavior |
|---|---|
| `Performer.Update.Post` | On every performer update, mirror the favorite flag to StashDB (`favoritePerformer` mutation) and set the matching Whisparr performer's `monitored` flag to match. |

### Tasks

| Task | What it does |
|---|---|
| **Hub: Status** | Performer counts and connection health across Stash, StashDB, and Whisparr. |
| **Sync: All Favorites** | Union reconcile — every favorite present in Stash **or** StashDB is favorited in both, and matching Whisparr performers are `monitored=true`. Idempotent. |
| **Sync: Report Diffs (dry-run)** | Report-only diff of favorites across Stash / StashDB / Whisparr. Never writes. |
| **Sync: Monitor Whisparr (bulk)** | Catchup task: for every current favorite, set the matching Whisparr performer's `monitored` flag. No Stash/StashDB writes. |
| **Enrich: Missing Metadata** | Gap-fill performer metadata from StashDB on favorites without a StashDB UUID. Fills `birthdate`, `country`, `ethnicity`, eye/hair colour, `height_cm`, `career_length`, `alias_list`, `gender`, `disambiguation`. **Never overwrites existing fields.** Attaches the StashDB UUID on success. |
| **Enrich: All Favorites** | Same as above but runs on every favorite, including those already linked. |
| **UI: Stashbox Performer Scene Count** | Internal — called by `ui.js` for the performer badge. |
| **UI: Stashbox Studio Scene Count** | Internal — called by `ui.js` for the studio badge. |

### UI overlay

`ui.js` injects a `local / stashbox` scene-count badge next to each StashDB ID pill on performer and studio detail pages. Toggleable per entity type via settings (default off — opt-in).

## Settings

Configure in **Stash → Settings → Plugins → Performer Hub**. Settings are grouped visually by prefix in the display name (`[Connections]`, `[Sync]`, `[Enrich]`, `[UI]`).

| Key | Type | Default | Purpose |
|---|---|---|---|
| `stashdb_api_key` | string | — | StashDB API key (same one used in Stash's stash-box config) |
| `whisparr_url` | string | — | Whisparr base URL, no trailing slash |
| `whisparr_api_key` | string | — | Whisparr API key |
| `dry_run_sync` | boolean | true | Preview sync writes without applying |
| `dry_run_enrich` | boolean | true | Preview enrich writes without applying |
| `rate_limit_seconds` | number | 2 | Delay between StashDB API calls during enrich |
| `scene_count_performers` | boolean | false | Show scene-count badge on performer pages |
| `scene_count_studios` | boolean | false | Show scene-count badge on studio pages |
| `include_subsidiary_studios` | boolean | false | Studio scene count includes child studios |

## Architecture

```
performer_hub/
├── performer_hub.yml   # Stash plugin manifest
├── performer_hub.py    # entry point + mode dispatch
├── ui.js               # vanilla-JS scene-count overlay
├── lib/
│   ├── common.py       # urllib wrappers, Stash log protocol
│   ├── stash.py        # Stash GraphQL client
│   ├── stashdb.py      # StashDB GraphQL client
│   ├── whisparr.py     # Whisparr REST client (monitor-flag only)
│   ├── sync.py         # hook + sync-all + monitor-bulk + report handlers
│   └── enrich.py       # gap-fill enrichment handler
└── README.md
```

**No external dependencies.** All HTTP goes through `urllib` (Python stdlib). The UI uses `fetch` + `MutationObserver` — no `stashUserscriptLibrary7dJx1qP` or any other plugin required.

## Whisparr integration: monitor-flag approach

Performer Hub uses one Whisparr integration model: **set `monitored=true` on the existing Whisparr performer entity**. Whisparr pulls scenes via its own native StashDB ImportList on that performer.

> This plugin does **not** manage TPDB-based ImportLists. If you previously used `performer_tools`' TPDB ImportList flow, remove those lists from Whisparr before enabling Performer Hub's sync — otherwise Whisparr catalogues scenes through two parallel channels and creates duplicates.

## Migration from FavSync + Performer Tools

1. Install Performer Hub from the teka6 plugin source.
2. Copy settings from the old plugins into Performer Hub's settings (same keys, snake_case).
3. Run **Hub: Status** to verify all three services are connected.
4. Run **Sync: Report Diffs (dry-run)** for a sanity check.
5. Uninstall the old `favsync` and `performer_tools` plugins.

## Version

`0.2.0` — continues FavSync's version line with the Performer Tools merge.
