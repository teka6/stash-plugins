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

### FavSync — Stash ↔ StashDB ↔ Whisparr

Keeps performer favorites in sync across all three systems. Native Stash plugin, no external dependencies beyond Python 3.

- **Hook** on `Performer.Update.Post` — auto-sync favorite toggles
- **Task** `Sync All Favorites` — union reconcile
- **Task** `Report Diffs (dry-run)` — diagnose mismatches
- **Task** `Monitor All Favs in Whisparr (bulk)` — bulk set Whisparr performer monitored flag

See [`favsync/README.md`](./favsync/README.md) for details.

### Performer Tools

Performer metadata enrichment and favorites sync with a UI overlay.

See [`performer-plugin/README.md`](./performer-plugin/README.md) for details.

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
