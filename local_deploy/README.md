# Local Deploy

`deploy_all.py` copies or links each package into an r2modman profile so the running
game picks up source changes. Run it from the shell repo root:

```bash
ModpackTools/run ModpackTools/local_deploy/deploy_all.py [--fast | --overwrite]
```

## Which flag to use

| Situation | Flag | Why |
|---|---|---|
| First deploy into a profile, or a new package was added | none | Creates missing entries, skips ones that exist. |
| Edit → test loop while the game is running | `--fast` | Copies only files whose size/mtime changed since the last deploy. Fast, and never deletes a plugin directory. |
| Profile looks wrong: manual edits, cache drift, a package renamed, or `--fast` says "unchanged" but the game disagrees | `--overwrite` | Re-syncs every file from source and removes stale ones. Since the in-place sync change it also never deletes a plugin directory. |
| Switching between symlink and copy mode | `--overwrite` with `--link-mode` | Replaces a symlink with a copied tree or vice versa. |

`--fast` and `--overwrite` cannot be combined. `--fast` only works when link mode
resolves to `copy` (WSL deploying into a Windows profile does this automatically).

## Why "never delete a plugin directory" matters

ReturnOfModding hot reload works through a Windows directory watcher
(`ReadDirectoryChangesW`) that holds one handle per plugin directory. Deleting that
directory, even if it is immediately recreated, invalidates the handle. The game log
then shows `ReadDirectoryChangesW failed: 5` and no file change is noticed again until
the game restarts. Edits look like they "need a hard reload".

Both `--fast` and `--overwrite` now sync files inside the existing directory. The only
case that still replaces a directory is converting a symlink into a copy or the
reverse. Do that with the game closed.

## Other rules of thumb

- After deploying `adamant-ModpackLib` itself, restart the game. In-process Lib reload is
  best-effort; modules keep closures from the Lib generation they activated against.
- `--fast` trusts its cache in `<profile>/ReturnOfModding/.adamant-deploy-cache/`. Delete
  that folder or run `--overwrite` if a deploy claims nothing changed after an edit.
- Deploy runs `tests/smoke.lua` first when the shell repo has one. Use `--skip-smoke`
  only when debugging the deploy itself.
- Keep `thunderstore.toml` and `manifest.json` consistent before deploying; the manifest
  step regenerates manifests and `--overwrite` is required to replace existing ones.
