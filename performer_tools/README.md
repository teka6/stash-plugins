# Performer Tools

Native Stash plugin for performer metadata enrichment, favorites sync across Stash/StashDB/Whisparr, and in-UI scene-count badges.

Complementary to [FavSync](../favsync/) — different approach to Whisparr integration and richer metadata features.

## Features

### Tasks

| Task | What it does |
|---|---|
| **Performer Status** | Shows counts across Stash, StashDB, and Whisparr (total / favorites / with StashDB ID / with TPDB ID, plus StashDB & Whisparr connection status). |
| **Sync Favorites** | Bidirectional: pushes Stash favorites to StashDB, pulls StashDB favorites to Stash, and creates TPDB-based Whisparr ImportLists for each favorite that has a TPDB ID. |
| **Sync StashDB Only** | Same as above but skips the Whisparr step. |
| **Enrich Performers** | Gap-fills missing metadata on performers without a StashDB UUID by searching StashDB by name. Fills birthdate, country, ethnicity, eye/hair colour, height, career span, aliases, disambiguation, gender. Also attaches the StashDB UUID on success. **Never overwrites existing fields** — only fills blanks. |
| **Enrich All Favorites** | Same as Enrich but runs on all favorites (not just those missing a StashDB UUID). |
| **Get Stashbox Performer Scene Count** | Called by the UI overlay — returns `{local}/{stashbox}` counts for the badge. |
| **Get Stashbox Studio Scene Count** | Same for studios; honours the `includeSubsidiaryStudios` setting. |

### UI overlay

`performer_tools_ui.js` injects a small badge next to StashDB IDs on performer and studio pages, showing `local_scene_count / stashbox_scene_count`. Toggleable per entity type via the settings.

Requires the [stashUserscriptLibrary7dJx1qP](https://github.com/7dJx1qP/stash-plugins) plugin for UI plumbing.

## Settings

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `dryRun` | boolean | true | Preview changes without writing. Keep on until confident. |
| `stashdbApiKey` | string | — | StashDB API key (same one Stash uses in its stash-box config). |
| `sceneCountPerformers` | boolean | false | Show scene-count badge on performer pages. |
| `sceneCountStudios` | boolean | false | Show scene-count badge on studio pages. |
| `includeSubsidiaryStudios` | boolean | false | For studio scene counts, include child studios. |
| `whisparrUrl` | string | — | Whisparr base URL (e.g. `https://whisparrv3.example.com`). |
| `whisparrApiKey` | string | — | Whisparr API key. |
| `whisparrRootFolder` | string | — | Root folder for ImportLists created by the sync task. |
| `whisparrQualityProfile` | number | 8 | Quality profile ID for new ImportLists. |
| `rateLimitSeconds` | number | 2 | Delay between StashDB API calls. |

## FavSync vs Performer Tools — which one?

Both cover Stash ↔ StashDB favorites sync. The Whisparr side is different:

|  | FavSync | Performer Tools |
|---|---|---|
| Favorites sync | Hook-driven, automatic on toggle | Task-driven, manual |
| Whisparr integration | Sets `monitored=true` on existing Whisparr performer entity (native StashDB ImportList pulls scenes) | Creates one TPDB-based ImportList per favorite (Whisparr pulls via TPDB) |
| Metadata enrichment | No | Yes (gap-fill from StashDB) |
| UI overlay (scene-count badges) | No | Yes |
| Dependencies | Python stdlib only | `requests` + `stashUserscriptLibrary7dJx1qP` |
| Invocation | Automatic (hook) + manual tasks | Manual tasks only |

Run both simultaneously if you want:
- Hook-driven favorites propagation (FavSync)
- Plus enrichment & UI badges (Performer Tools)
- Plus TPDB-based Whisparr ImportLists (Performer Tools) *alongside* Whisparr native StashDB monitoring (via FavSync)

If Whisparr pulls from both channels for the same performer you'll get duplicate catalogue entries. In that case pick one approach for Whisparr.

## Dependencies

- Python 3.9+
- `requests` (pip install requests)
- `stashUserscriptLibrary7dJx1qP` Stash plugin (UI)

## Version

1.1.0
