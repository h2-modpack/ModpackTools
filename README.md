# ModpackTools

Shared platform tooling for Hades II modpack shell repos. ModpackTools owns the
repo automation layer: deployment, module scaffolding, release preparation,
release dispatch, and maintenance validation helpers.

This repo is usually checked out as `ModpackTools/` inside a shell repo. Use
[`ModpackBootstrap`](https://github.com/h2-modpack/ModpackBootstrap) for the
one-time creation of a new pack workspace.

New users should start with the
[`ModpackBootstrap` Getting Started guide](https://github.com/h2-modpack/ModpackBootstrap/blob/main/docs/GETTING_STARTED.md),
then return here for ongoing pack maintenance commands.

## Role In The Platform

ModpackTools is load-bearing shared infrastructure, not only a convenience
script collection. Child package release workflows use tagged ModpackTools refs
for release preparation and Thunderstore package-state checks, so release tags
such as `release-tools-V3` are the compatibility contract for publishing.

Shell repos are still the canonical ecosystem validation boundary. A shell repo
checks out Lib, the coordinator, modules, and ModpackTools together. Smoke
validation should stay shell-owned by passing a shell manifest to Lib's smoke
runner; ModpackTools should not own pack smoke layouts. ModpackTools' own CI
stays self-contained and validates the tools repo without depending on one
specific consumer shell.

## What This Handles

ModpackTools is the ongoing platform SDK for an existing pack workspace:

- scaffold new module repos from the module template
- stage package assets for local deploy, generate manifests, link profiles, and configure git hooks
- register or prune submodule entries
- validate release plans and platform dependency edges

## Self Tests

Run the Tools-owned test entrypoint from the ModpackTools repo root:

```bash
python3 tests/all.py
```

This is the same command used by ModpackTools CI. Shell smoke tests and
pack-wide assembled-checkout sweeps stay outside Tools CI.

## Add A Module

From the shell repo root:

```bash
ModpackTools/run ModpackTools/new_module/create.py --package-id My_Module --title "My Module"
ModpackTools/run ModpackTools/local_deploy/deploy_all.py --overwrite
```

On Windows Command Prompt or PowerShell, use `ModpackTools\run.bat` instead of
`ModpackTools/run`. The launcher picks an available Python 3 command
(`python3`, `python`, or `py -3`).

`create.py` creates the GitHub repo from
[`ModpackModuleTemplate`](https://github.com/h2-modpack/ModpackModuleTemplate),
fills in module identity, commits the initial repo, registers it under
`Submodules/`, and syncs the coordinator dependency block. Shell smoke derives
the module roster from `.gitmodules`.

Generated module package names do not include the pack prefix because the pack
team already carries that identity. For example, in a pack using
`team = adamantSpeedrun`, `--package-id LiveSplit --title "LiveSplit"` creates
`adamantSpeedrun-LiveSplit`. The package id is also the Lib module id.

## Local Deployment

After cloning, scaffolding, or changing a pack locally, run the deploy step from
the shell repo root:

```bash
ModpackTools/run ModpackTools/local_deploy/deploy_all.py
ModpackTools/run ModpackTools/local_deploy/deploy_all.py --overwrite
```

`deploy_all.py` stages each package's root `icon.png` and `LICENSE` into
`src/`, refreshes generated manifests, updates r2modman profile links, and
configures git hooks. Use `--overwrite` when regenerating files or links that
already exist.

When the shell repo has `tests/smoke.lua`, deployment first runs the same smoke
command used by shell CI:

```bash
lua tests/smoke.lua
```

If smoke fails, deployment stops before writing to the live profile. Use
`--skip-smoke` only for exceptional debugging cases.

When running from WSL with a Windows r2modman profile, deployment resolves the
profile through `APPDATA` or by querying Windows directly, then copies package
folders into the Windows profile. Same-side deploys keep using symlinks by
default. Use `--link-mode copy` or `--link-mode symlink` to force one mode, and
`--profile-root PATH` when the profile directory cannot be discovered
automatically.

Use `--fast` for WSL-to-Windows edit/test loops when link mode resolves to
`copy`. It keeps a profile-local deploy cache and copies only source files whose
cached metadata changed. It trusts the previous deploy cache and does not verify
destination contents; use `--overwrite` to repair a profile after manual edits,
cache drift, or suspicious live state.

## Local Checkout Tests

To run a broader local assembled-checkout sweep from the shell repo root:

```bash
ModpackTools/run ModpackTools/local_test/all.py
```

This is local convenience only and is not part of ModpackTools CI. It runs the
same shell smoke command as shell CI, then runs each registered repo's declared
`tests/all.lua` or `tests/all.py` entrypoint when present. It does not discover
arbitrary test files.

## Remote Maintenance And Release

Normal release maintenance is:

1. Module, coordinator, or Lib repos change and are pushed.
2. Update and commit the shell submodule pointers as part of that development work.
3. Run `Release All` from the shell repo when publishing the pack.

`Release All` validates the checked-out shell snapshot before dispatching child
release workflows. The dependency validator checks that required Thunderstore
dependency edges exist, and prints the checked-out package versions plus current
source pins for auditability. The release dispatcher can also verify that each
selected child repo matches the configured release branch head and has a
successful `ci.yaml` run for that commit. It does not require exact dependency
pin equality because Thunderstore resolves package dependencies to the latest
available version.

Child release workflows should receive an explicit `tools-ref` from the shell
release workflow. Use a tagged ModpackTools ref for real releases; reserve
floating branches for local development or temporary dry-run testing.

Before publishing releases, also add these GitHub Actions org secrets with
**All repositories** access:

- `TCLI_AUTH_TOKEN`
- `RELEASE_DISPATCH_TOKEN`

The GitHub PAT value for `RELEASE_DISPATCH_TOKEN` is still created manually in
GitHub user settings.

With All repositories access, every repo in the pack org can read the secrets.
The shell, coordinator, and module repos are covered automatically.

## Submodule Maintenance

If repos already exist under `Submodules/` but are not registered in
`.gitmodules`:

```bash
ModpackTools/run ModpackTools/new_module/register_submodules.py
```

After deleting module folders from `Submodules/`, prune stale entries:

```bash
ModpackTools/run ModpackTools/new_module/register_submodules.py --prune
```

Both register and prune flows refresh the coordinator dependency block. Shell
smoke derives the module roster directly from `.gitmodules`.

To commit and push a shared change across all module repos:

```bash
ModpackTools/run ModpackTools/commit_submodules.py "fix: update config schema"
```

Clean module repos are skipped.

## File Reference

Entry points:

| File | Description |
|---|---|
| `commit_submodules.py` | Commit and push all changed `Submodules/*` repos with one message |

Deployment helpers:

| File | Description |
|---|---|
| `local_deploy/deploy_all.py` | Full local deploy: smoke preflight, staged package assets, manifests, symlinks, git hooks |
| `local_deploy/steps/` | Implementation modules used by `deploy_all.py` |
| `local_test/all.py` | Local assembled-checkout sweep driven by the registered shell composition |

GitHub automation helpers:

| File | Description |
|---|---|
| `github/release_all.py` | Shared pack-wide release dispatcher used by shell `Release All` workflows |
| `github/prepare_package_release.py` | Prepare package-local changelog, release notes, and version files from conventional commits |
| `tests/all.py` | Run ModpackTools-owned compile, Python test, and Lua test-parse checks |
| `tests/test_release_all.py` | Dry-run planner tests for release target normalization and version gating |
| `validate_platform_versions.py` | Validate required platform dependency edges and print the checked-out version snapshot |

Scaffolding helpers:

| File | Description |
|---|---|
| `new_module/create.py` | Scaffold a module repo from the template and register it as a submodule |
| `new_module/register_submodules.py` | Register or prune `Submodules/` entries in `.gitmodules` |
| `new_module/coordinator_deps.py` | Sync the coordinator's managed module dependency block |
| `new_module/module_roster.py` | Shared discovery for registered module package metadata |

## Common Flags

Most scripts share these flags:

| Flag | Default | Description |
|---|---|---|
| `--overwrite` | off | Overwrite existing files or links instead of skipping |
| `--profile NAME` | `h2-dev` | r2modman profile to deploy into |
| `--profile-root PATH` | auto | Directory containing r2modman profile folders |
| `--link-mode auto\|symlink\|copy` | `auto` | Use symlinks, copy folders, or auto-detect WSL-to-Windows copy deploy |
| `--skip-smoke` | off | Skip deploy-time shell smoke preflight |
| `--dry-run` | off | Show what would happen without making changes, where supported |
