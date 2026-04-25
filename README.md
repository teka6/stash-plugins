# teka6's Stash Plugins

Native [Stash](https://github.com/stashapp/stash) plugins that integrate with [StashDB](https://stashdb.org) and [Whisparr](https://github.com/Whisparr/Whisparr).

## Installation via Stash plugin registry

In Stash, go to **Settings → Plugins → Available Plugins → Add Source**:

```
Name: teka6
Source URL: https://teka6.github.io/stash-plugins/main/index.yml
```

Then **Available Plugins** lists the plugins below. Install the ones you want, reload plugins, configure their settings.

## Plugins

### Performer Hub

Unified plugin for performer favorites sync (Stash ↔ StashDB ↔ Whisparr via monitor-flag), StashDB metadata gap-fill, and in-UI scene-count badges. Native Stash plugin with zero external dependencies — Python 3 stdlib + vanilla JavaScript.

- **Hook** on `Performer.Update.Post` — auto-sync favorite toggles to StashDB and Whisparr
- **Tasks**: `Hub: Status`, `Sync: All Favorites`, `Sync: Report Diffs (dry-run)`, `Sync: Monitor Whisparr (bulk)`, `Enrich: Missing Metadata`, `Enrich: All Favorites`
- **UI overlay**: `local / stashbox` scene-count badges on performer and studio pages (opt-in per type)

See [`performer_hub/README.md`](./performer_hub/README.md) for full details.

Performer Hub is the successor to the previous `favsync` and `performer_tools` plugins, which were merged into a single dependency-free plugin. If you previously had either installed, uninstall it before enabling Performer Hub to avoid duplicate sync writes.

## Development

Plugins are authored as plain directories with a YAML manifest and a Python entry point:

```
<plugin>/
├── <plugin>.yml   # Stash plugin manifest
├── <plugin>.py    # entry point
└── README.md
```

The GitHub Action at `.github/workflows/publish.yml` bundles each plugin into a `.zip` on push to `main` and regenerates `docs/index.yml`, which is served via GitHub Pages.

## License

[MIT](./LICENSE)
