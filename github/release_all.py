#!/usr/bin/env python3
"""Coordinate pack-wide release workflow dispatches.

This script is intentionally pack-parameterized so shell workflow YAML only
passes pack identity while the release planning and dispatch behavior remains
testable in ModpackTools.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


GITHUB_DIR = Path(__file__).resolve().parent
TOOLS_DIR = GITHUB_DIR.parent
ROOT_DIR = TOOLS_DIR.parent
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

sys.path.insert(0, str(GITHUB_DIR))
import prepare_package_release as prep  # noqa: E402


class ReleaseError(Exception):
    def __init__(self, title: str, message: str):
        super().__init__(message)
        self.title = title
        self.message = message


@dataclass(frozen=True)
class ReleaseConfig:
    org: str
    team: str
    coordinator_repo: str
    dependency_repo: str | None = None
    dependency_org: str | None = None
    root: Path = ROOT_DIR
    workflow: str = "release.yaml"
    branch: str = "main"
    poll_attempts: int = 30
    poll_interval: int = 5

    @property
    def module_prefix(self) -> str:
        return f"{self.team}-"


@dataclass(frozen=True)
class ReleasePlan:
    module_repos: list[str]
    coordinator_selected: bool

    def total(self) -> int:
        return len(self.module_repos) + (1 if self.coordinator_selected else 0)


def parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def validate_tag(tag: str, targeted: bool) -> None:
    if not VERSION_PATTERN.match(tag or ""):
        raise ReleaseError(
            "Invalid package version number",
            "Version numbers must follow the Major.Minor.Patch format (e.g. 1.45.320).",
        )

    patch = tag.split(".")[2]
    if targeted:
        if patch == "0":
            raise ReleaseError(
                "Invalid Hotfix Version",
                "Targeted hotfixes must use a patch version greater than .0 (e.g. 1.2.1).",
            )
    elif patch != "0":
        raise ReleaseError(
            "Invalid Mass Release Version",
            "Mass releases (all modules) must end in .0 (e.g., 1.2.0) to reserve patch numbers for hotfixes.",
        )


def discover_module_repos(config: ReleaseConfig) -> list[str]:
    submodules = config.root / "Submodules"
    if not submodules.is_dir():
        return []

    repos: list[str] = []
    for entry in sorted(submodules.iterdir(), key=lambda path: path.name.lower()):
        if not entry.is_dir():
            continue
        if not entry.name.startswith(config.module_prefix):
            continue
        if not (entry / "thunderstore.toml").is_file() and not (entry / "src").is_dir():
            continue
        repos.append(entry.name)
    return repos


def _casefold_map(values: list[str]) -> dict[str, str]:
    return {value.casefold(): value for value in values}


def coordinator_aliases(config: ReleaseConfig) -> set[str]:
    aliases = {
        "Coordinator",
        "Modpack",
        config.coordinator_repo,
    }

    team_prefix = f"{config.team}-"
    if config.coordinator_repo.startswith(team_prefix):
        aliases.add(config.coordinator_repo[len(team_prefix):])

    return aliases


def dependency_aliases(config: ReleaseConfig) -> set[str]:
    aliases = {
        "Dependency",
        "Lib",
        "ModpackLib",
        "adamant-ModpackLib",
    }
    if config.dependency_repo:
        aliases.add(config.dependency_repo)
        if config.dependency_repo.startswith("adamant-"):
            aliases.add(config.dependency_repo[len("adamant-"):])

    return aliases


def module_aliases(config: ReleaseConfig, repo: str) -> set[str]:
    aliases = {repo}
    team_prefix = f"{config.team}-"
    if repo.startswith(team_prefix):
        package_name = repo[len(team_prefix):]
        aliases.add(package_name)
    return aliases


def normalize_release_target(
    raw: str,
    config: ReleaseConfig,
    available_module_repos: list[str],
) -> tuple[str, str] | None:
    target = (raw or "").strip()
    if not target:
        return None

    if target.casefold() in {alias.casefold() for alias in coordinator_aliases(config)}:
        return ("coordinator", config.coordinator_repo)

    if target.casefold() in {alias.casefold() for alias in dependency_aliases(config)}:
        raise ReleaseError(
            "Unsupported release target",
            "ModpackLib is shared infra and is not released by pack release_all. "
            "Release Lib from its own repository, then pass lib-version to pin pack dependencies.",
        )

    alias_to_repo: dict[str, str] = {}
    for repo in available_module_repos:
        for alias in module_aliases(config, repo):
            alias_to_repo[alias.casefold()] = repo

    direct = alias_to_repo.get(target.casefold())
    if direct:
        return ("module", direct)

    candidates = [target]
    if target.startswith(config.team + "-"):
        candidates.append(target)
    else:
        candidates.append(f"{config.module_prefix}{target}")

    repo_map = _casefold_map(available_module_repos)
    for candidate in candidates:
        repo = repo_map.get(candidate.casefold())
        if repo:
            return ("module", repo)

    if target.startswith(config.team + "-"):
        normalized = target
    else:
        normalized = f"{config.module_prefix}{target}"
    raise ReleaseError(
        "Unknown release target",
        f"{normalized} is not a checked-out module repo.",
    )


def build_release_plan(
    config: ReleaseConfig,
    tag: str | None,
    targets: str | None,
    available_module_repos: list[str] | None = None,
) -> ReleasePlan:
    requested_filter = (targets or "").strip()
    targeted = bool(requested_filter)
    if not targeted and tag is not None:
        validate_tag(tag, targeted)

    available_modules = (
        discover_module_repos(config)
        if available_module_repos is None
        else list(available_module_repos)
    )
    module_repos: list[str] = []
    seen_modules: set[str] = set()
    coordinator_selected = False

    if not targeted:
        return ReleasePlan(
            module_repos=available_modules,
            coordinator_selected=True,
        )

    for requested in requested_filter.split(","):
        normalized = normalize_release_target(requested, config, available_modules)
        if not normalized:
            continue

        kind, repo = normalized
        if kind == "coordinator":
            coordinator_selected = True
        elif repo not in seen_modules:
            seen_modules.add(repo)
            module_repos.append(repo)

    plan = ReleasePlan(
        module_repos=module_repos,
        coordinator_selected=coordinator_selected,
    )
    if plan.total() == 0:
        raise ReleaseError(
            "No release targets",
            "No module or coordinator release targets were selected.",
        )
    if tag is not None:
        validate_tag(tag, targeted)
    return plan


def print_plan(plan: ReleasePlan, config: ReleaseConfig) -> None:
    print("Release order:")
    for repo in plan.module_repos:
        print(f"  - {repo}")
    if plan.coordinator_selected:
        print(f"  - {config.coordinator_repo}")


def parse_repo_fields(raw_fields: list[str] | None) -> dict[str, list[str]]:
    repo_fields: dict[str, list[str]] = {}
    for raw in raw_fields or []:
        value = (raw or "").strip()
        if not value:
            continue

        repo, separator, field = value.partition(":")
        if not separator or not repo.strip() or not field.strip() or "=" not in field:
            raise ReleaseError(
                "Invalid repo workflow field",
                "Repo workflow fields must use the repo:key=value format.",
            )

        repo_fields.setdefault(repo.strip(), []).append(field.strip())
    return repo_fields


def parse_workflow_fields(raw_fields: list[str] | None) -> list[str]:
    fields: list[str] = []
    for raw in raw_fields or []:
        field = (raw or "").strip()
        if not field:
            continue
        if "=" not in field:
            raise ReleaseError(
                "Invalid workflow field",
                "Workflow fields must use the key=value format.",
            )
        fields.append(field)
    return fields


def build_coordinator_dependency_pin_field(module_repos: list[str], tag: str) -> str | None:
    if not module_repos:
        return None
    pins = ",".join(f"{repo}={tag}" for repo in module_repos)
    return f"dependency-pins={pins}"


def merge_workflow_fields(
    repo: str,
    shared_fields: list[str] | None,
    repo_fields: dict[str, list[str]] | None,
) -> list[str]:
    fields = list(shared_fields or [])
    fields.extend((repo_fields or {}).get(repo, []))
    return fields


def has_workflow_field(fields: list[str], key: str) -> bool:
    return workflow_field_value(fields, key) is not None


def workflow_field_value(fields: list[str], key: str) -> str | None:
    expected = key.strip()
    for field in fields:
        field_key, separator, value = field.partition("=")
        if separator and field_key.strip() == expected:
            return value.strip()
    return None


@dataclass(frozen=True)
class VersionPlanEntry:
    repo: str
    kind: str  # "module" or "coordinator"
    last_tag: str | None
    commit_count: int
    bump: str | None
    version: str | None  # None means the repo is skipped
    source: str  # "commits", "override", "lockstep", "modules", "none"

    @property
    def released(self) -> bool:
        return self.version is not None


def parse_version_overrides(
    raw: str | None,
    config: ReleaseConfig,
    available_module_repos: list[str],
) -> dict[str, str]:
    """Parse 'Target=Major.Minor.Patch,...' into {repo: version}, resolving target aliases."""
    overrides: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        target, separator, version = item.partition("=")
        target = target.strip()
        version = version.strip()
        if not separator or not target or not VERSION_PATTERN.match(version):
            raise ReleaseError(
                "Invalid version override",
                f"'{item}' must use Target=Major.Minor.Patch (e.g. LiveSplit=6.0.0).",
            )
        normalized = normalize_release_target(target, config, available_module_repos)
        if normalized is None:
            continue
        overrides[normalized[1]] = version
    return overrides


def repo_release_state(config: ReleaseConfig, repo: str) -> tuple[str | None, int, str | None]:
    """Return (last tag, commits since it, implied bump) for a checked-out repo."""
    repo_path = release_repo_path(config, repo)
    try:
        last_tag, commits = prep.commits_since_latest_tag(repo_path)
    except subprocess.CalledProcessError as exc:
        raise ReleaseError(
            "Invalid release checkout",
            f"Could not read release history for {repo} at {repo_path}.",
        ) from exc
    return last_tag, len(commits), prep.classify_bump(commits)


def plan_versions(
    config: ReleaseConfig,
    plan: ReleasePlan,
    lockstep_tag: str | None = None,
    overrides: dict[str, str] | None = None,
) -> list[VersionPlanEntry]:
    """Resolve the version each selected repo will release, skipping repos with nothing to ship."""
    overrides = overrides or {}
    entries: list[VersionPlanEntry] = []
    module_bumps: list[str | None] = []

    for repo in plan.module_repos:
        last_tag, count, bump = repo_release_state(config, repo)
        if lockstep_tag is not None:
            version, source = lockstep_tag, "lockstep"
        elif repo in overrides:
            version, source = overrides[repo], "override"
        elif bump is not None:
            version, source = prep.bump_version(last_tag, bump), "commits"
        else:
            version, source = None, "none"
        if version is not None and source != "lockstep":
            module_bumps.append(bump or "patch")
        entries.append(VersionPlanEntry(repo, "module", last_tag, count, bump, version, source))

    if plan.coordinator_selected:
        repo = config.coordinator_repo
        last_tag, count, own_bump = repo_release_state(config, repo)
        released_modules = any(entry.released for entry in entries)
        if lockstep_tag is not None:
            version, bump, source = lockstep_tag, own_bump, "lockstep"
        elif repo in overrides:
            version, bump, source = overrides[repo], own_bump, "override"
        elif released_modules or own_bump is not None:
            # At least a patch whenever any module ships; otherwise the largest bump wins.
            bump = prep.max_bump(own_bump, *module_bumps) or "patch"
            version = prep.bump_version(last_tag, bump)
            source = "modules" if released_modules else "commits"
        else:
            version, bump, source = None, None, "none"
        entries.append(VersionPlanEntry(repo, "coordinator", last_tag, count, bump, version, source))

    return entries


def build_coordinator_version_pins(
    config: ReleaseConfig,
    entries: list[VersionPlanEntry],
    available_module_repos: list[str],
) -> dict[str, str]:
    """Pin every module: released ones to their new version, the rest to their latest tag."""
    released = {entry.repo: entry.version for entry in entries if entry.kind == "module" and entry.version}
    pins: dict[str, str] = {}
    for repo in available_module_repos:
        if repo in released:
            pins[repo] = released[repo]
            continue
        latest = prep.latest_version_tag(release_repo_path(config, repo))
        if latest:
            pins[repo] = latest
    return pins


def build_dependency_pins_field(pins: dict[str, str]) -> str | None:
    if not pins:
        return None
    return "dependency-pins=" + ",".join(f"{repo}={version}" for repo, version in pins.items())


def format_version_plan(entries: list[VersionPlanEntry]) -> str:
    rows = [("Repo", "Last tag", "Commits", "Bump", "Next version")]
    for entry in entries:
        rows.append(
            (
                entry.repo,
                entry.last_tag or "-",
                str(entry.commit_count),
                entry.bump or "-",
                entry.version if entry.version else f"skip ({entry.source})",
            )
        )
        if entry.version and entry.source in {"override", "lockstep"}:
            rows[-1] = rows[-1][:-1] + (f"{entry.version} ({entry.source})",)
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    return "\n".join(
        "  " + "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip()
        for row in rows
    )


def write_step_summary(entries: list[VersionPlanEntry]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = ["### Release plan", "", "| Repo | Last tag | Commits | Bump | Next version |", "| --- | --- | --- | --- | --- |"]
    for entry in entries:
        version = entry.version if entry.version else f"skip ({entry.source})"
        lines.append(
            f"| {entry.repo} | {entry.last_tag or '-'} | {entry.commit_count} | {entry.bump or '-'} | {version} |"
        )
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def print_version_plan(entries: list[VersionPlanEntry]) -> None:
    print("Release plan:")
    print(format_version_plan(entries))
    write_step_summary(entries)


def run_gh(args: list[str], capture_json: bool = False) -> object | None:
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=capture_json,
        text=True,
    )
    if not capture_json:
        return None
    output = result.stdout.strip()
    if not output:
        return None
    return json.loads(output)


def run_gh_text(args: list[str]) -> str:
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def release_exists(config: ReleaseConfig, repo: str, tag: str) -> bool:
    result = subprocess.run(
        [
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            f"{config.org}/{repo}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True

    output = f"{result.stdout}\n{result.stderr}".casefold()
    if "not found" in output or "could not find" in output:
        return False

    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )


def release_repos(config: ReleaseConfig, plan: ReleasePlan) -> list[str]:
    repos = list(plan.module_repos)
    if plan.coordinator_selected:
        repos.append(config.coordinator_repo)
    return repos


def release_repo_path(config: ReleaseConfig, repo: str) -> Path:
    if repo == config.coordinator_repo:
        return config.root / repo
    return config.root / "Submodules" / repo


def short_sha(sha: str) -> str:
    return sha[:12]


def local_repo_head(config: ReleaseConfig, repo: str) -> str:
    repo_path = release_repo_path(config, repo)
    if not repo_path.is_dir():
        raise ReleaseError(
            "Missing release checkout",
            f"{repo} is selected for release but {repo_path} does not exist in the shell checkout.",
        )

    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ReleaseError(
            "Invalid release checkout",
            f"Could not read local git HEAD for {repo} at {repo_path}.",
        ) from exc
    return result.stdout.strip()


def remote_branch_head(config: ReleaseConfig, repo: str) -> str:
    branch = quote(config.branch, safe="")
    sha = run_gh_text(
        [
            "api",
            f"repos/{config.org}/{repo}/branches/{branch}",
            "--jq",
            ".commit.sha",
        ]
    )
    if not sha:
        raise ReleaseError(
            "Missing release branch",
            f"Could not resolve {config.org}/{repo}@{config.branch}.",
        )
    return sha


def successful_ci_run_for_commit(config: ReleaseConfig, repo: str, sha: str) -> int | None:
    data = run_gh(
        [
            "run",
            "list",
            "--repo",
            f"{config.org}/{repo}",
            "--workflow",
            "ci.yaml",
            "--commit",
            sha,
            "--status",
            "success",
            "--limit",
            "1",
            "--json",
            "databaseId",
        ],
        capture_json=True,
    )
    if not isinstance(data, list) or not data:
        return None
    database_id = data[0].get("databaseId")
    return int(database_id) if database_id is not None else None


def verify_repo_ci(config: ReleaseConfig, repo: str, tag: str, child_dry_run: bool) -> None:
    full_repo = f"{config.org}/{repo}"
    if not child_dry_run and release_exists(config, repo, tag):
        print(f"--- CI preflight skipped: {full_repo} already has release {tag} ---")
        return

    local_sha = local_repo_head(config, repo)
    branch_sha = remote_branch_head(config, repo)
    if local_sha != branch_sha:
        raise ReleaseError(
            "Release ref mismatch",
            f"{full_repo} is checked out at {short_sha(local_sha)}, but {config.branch} is "
            f"{short_sha(branch_sha)}. Update the shell checkout/submodule pointer before release_all.",
        )

    ci_run = successful_ci_run_for_commit(config, repo, branch_sha)
    if ci_run is None:
        raise ReleaseError(
            "CI has not passed",
            f"No successful ci.yaml run was found for {full_repo}@{short_sha(branch_sha)}. "
            "Push the commit and wait for CI before release_all.",
        )
    print(f"--- CI preflight passed: {full_repo}@{short_sha(branch_sha)} run {ci_run} ---")


def verify_release_plan_ci(
    config: ReleaseConfig,
    plan: ReleasePlan,
    tag: str,
    child_dry_run: bool,
) -> None:
    repos = release_repos(config, plan)
    if not repos:
        return

    print("")
    print("==========================================")
    print("  Release CI preflight")
    print("==========================================")
    for repo in repos:
        verify_repo_ci(config, repo, tag, child_dry_run)


def verify_version_plan_ci(
    config: ReleaseConfig,
    entries: list[VersionPlanEntry],
    child_dry_run: bool,
) -> None:
    released = [entry for entry in entries if entry.released]
    if not released:
        return

    print("")
    print("==========================================")
    print("  Release CI preflight")
    print("==========================================")
    for entry in released:
        verify_repo_ci(config, entry.repo, entry.version, child_dry_run)


def list_release_runs(config: ReleaseConfig, repo: str) -> list[dict]:
    data = run_gh(
        [
            "run",
            "list",
            "--repo",
            f"{config.org}/{repo}",
            "--workflow",
            config.workflow,
            "--branch",
            config.branch,
            "--limit",
            "50",
            "--json",
            "databaseId,createdAt,event,displayTitle",
        ],
        capture_json=True,
    )
    return data if isinstance(data, list) else []


def release_run_title(tag: str, child_dry_run: bool) -> str:
    return f"{'Dry-run' if child_dry_run else 'Release'} {tag}"


def build_dispatch_fields(
    tag: str,
    child_dry_run: bool,
    extra_fields: list[str] | None = None,
) -> list[str]:
    fields = [
        f"tag={tag}",
        f"is-dry-run={'true' if child_dry_run else 'false'}",
    ]
    fields.extend(extra_fields or [])
    return fields


def find_new_release_run(
    config: ReleaseConfig,
    repo: str,
    baseline_ids: set[int],
    earliest_created_at: datetime,
    expected_title: str,
) -> int | None:
    runs = list_release_runs(config, repo)
    candidates = []
    for run in runs:
        database_id = run.get("databaseId")
        if database_id in baseline_ids:
            continue
        if run.get("event") != "workflow_dispatch":
            continue
        if run.get("displayTitle") != expected_title:
            continue

        created_at_raw = run.get("createdAt")
        if created_at_raw:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            if created_at < earliest_created_at:
                continue

        candidates.append(run)

    if not candidates:
        return None

    candidates.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    return int(candidates[0]["databaseId"])


def dispatch_repo(
    config: ReleaseConfig,
    repo: str,
    tag: str,
    child_dry_run: bool,
    repo_fields: dict[str, list[str]] | None = None,
    shared_fields: list[str] | None = None,
) -> int:
    print(f"--- Triggering: {config.org}/{repo} ---")
    baseline_ids = {
        int(run["databaseId"])
        for run in list_release_runs(config, repo)
        if run.get("databaseId") is not None
    }
    dispatched_after = datetime.now(timezone.utc)

    command = [
        "workflow",
        "run",
        config.workflow,
        "--repo",
        f"{config.org}/{repo}",
        "--ref",
        config.branch,
    ]
    for field in build_dispatch_fields(
        tag,
        child_dry_run,
        merge_workflow_fields(repo, shared_fields, repo_fields),
    ):
        command.extend(["--field", field])

    run_gh(command)

    expected_title = release_run_title(tag, child_dry_run)
    for _ in range(config.poll_attempts):
        run_id = find_new_release_run(config, repo, baseline_ids, dispatched_after, expected_title)
        if run_id is not None:
            print(f"  Dispatched as run {run_id}.")
            return run_id
        time.sleep(config.poll_interval)

    raise ReleaseError(
        "Release dispatch failed",
        f"Failed to find dispatched workflow run for {config.org}/{repo}.",
    )


def watch_repo(config: ReleaseConfig, repo: str, run_id: int) -> None:
    print(f"--- Waiting: {config.org}/{repo} run {run_id} ---")
    run_gh(
        [
            "run",
            "watch",
            str(run_id),
            "--repo",
            f"{config.org}/{repo}",
            "--exit-status",
            "--interval",
            "10",
        ]
    )


def release_phase(
    config: ReleaseConfig,
    title: str,
    repos: list[str],
    tag: str,
    child_dry_run: bool,
    repo_fields: dict[str, list[str]] | None = None,
    shared_fields: list[str] | None = None,
) -> int:
    if not repos:
        return 0

    print("")
    print("==========================================")
    print(f"  {title}")
    print("==========================================")

    succeeded = 0
    for repo in repos:
        full_repo = f"{config.org}/{repo}"
        try:
            if not child_dry_run and release_exists(config, repo, tag):
                print(f"--- Skipping: {full_repo} already has release {tag} ---")
                succeeded += 1
                continue

            run_id = dispatch_repo(
                config,
                repo,
                tag,
                child_dry_run,
                repo_fields,
                shared_fields,
            )
            watch_repo(config, repo, run_id)
            succeeded += 1
        except (ReleaseError, subprocess.CalledProcessError) as exc:
            raise ReleaseError(
                "Release failed",
                f"{full_repo} failed during {title}; stopping before remaining repos. "
                f"Completed {succeeded} / {len(repos)} in this phase. {exc}",
            ) from exc

    return succeeded


def dispatch_release_plan(
    config: ReleaseConfig,
    plan: ReleasePlan,
    tag: str,
    child_dry_run: bool,
    repo_fields: dict[str, list[str]] | None = None,
    module_fields: list[str] | None = None,
    coordinator_fields: list[str] | None = None,
) -> None:
    succeeded = 0
    succeeded += release_phase(
        config,
        "Module releases",
        plan.module_repos,
        tag,
        child_dry_run,
        repo_fields,
        module_fields,
    )
    if plan.coordinator_selected:
        succeeded += release_phase(
            config,
            "Coordinator release",
            [config.coordinator_repo],
            tag,
            child_dry_run,
            repo_fields,
            coordinator_fields,
        )

    print("")
    print("==========================================")
    print(f"  Completed: {succeeded} / {plan.total()}")
    print("==========================================")


def dispatch_version_plan(
    config: ReleaseConfig,
    entries: list[VersionPlanEntry],
    child_dry_run: bool,
    repo_fields: dict[str, list[str]] | None = None,
    module_fields: list[str] | None = None,
    coordinator_fields: list[str] | None = None,
) -> None:
    released = [entry for entry in entries if entry.released]
    succeeded = 0
    for entry in released:
        modules = entry.kind == "module"
        succeeded += release_phase(
            config,
            f"{'Module' if modules else 'Coordinator'} release: {entry.repo} {entry.version}",
            [entry.repo],
            entry.version,
            child_dry_run,
            repo_fields,
            module_fields if modules else coordinator_fields,
        )

    print("")
    print("==========================================")
    print(f"  Completed: {succeeded} / {len(released)}")
    print("==========================================")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coordinate pack-wide release dispatches.")
    parser.add_argument("--org", required=True, help="GitHub org that owns the pack repos.")
    parser.add_argument("--team", required=True, help="Thunderstore team / repo prefix.")
    parser.add_argument("--coordinator-repo", required=True, help="Coordinator repo name.")
    parser.add_argument(
        "--dependency-repo",
        default=None,
        help="Deprecated compatibility option. Shared dependencies are not released by release_all.",
    )
    parser.add_argument(
        "--dependency-org",
        default=None,
        help="Deprecated compatibility option. Shared dependencies are not released by release_all.",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="Lockstep release version applied to every selected repo. Omit to compute per-repo versions from commits.",
    )
    parser.add_argument(
        "--versions",
        default="",
        help="Comma-separated Target=Major.Minor.Patch overrides for computed versions (e.g. LiveSplit=6.0.0).",
    )
    parser.add_argument("--targets", nargs="?", default="", const="", help="Comma-separated release targets. Blank means all.")
    parser.add_argument("--child-dry-run", default="false", help="Pass dry-run mode to child release workflows.")
    parser.add_argument("--plan-only", action="store_true", help="Validate and print the plan without dispatching.")
    parser.add_argument("--root", default=str(ROOT_DIR), help="Shell repo root. Defaults to parent of ModpackTools/.")
    parser.add_argument("--workflow", default="release.yaml", help="Child workflow filename.")
    parser.add_argument("--branch", default="main", help="Child workflow branch.")
    parser.add_argument(
        "--repo-field",
        action="append",
        default=[],
        help="Repo-specific workflow_dispatch field in repo:key=value format. May be repeated.",
    )
    parser.add_argument(
        "--module-field",
        action="append",
        default=[],
        help="workflow_dispatch field sent to every selected module repo in key=value format. May be repeated.",
    )
    parser.add_argument(
        "--coordinator-field",
        action="append",
        default=[],
        help="workflow_dispatch field sent to the coordinator repo in key=value format. May be repeated.",
    )
    parser.add_argument(
        "--pin-coordinator-module-deps",
        action="store_true",
        help="Pin selected coordinator module dependencies to the release tag using the dependency-pins workflow field.",
    )
    parser.add_argument(
        "--verify-ci",
        action="store_true",
        help="Before dispatch, require selected release repos to match the release branch head and have successful ci.yaml for that commit.",
    )
    return parser


def run_computed_release(
    config: ReleaseConfig,
    plan: ReleasePlan,
    args: argparse.Namespace,
    child_dry_run: bool,
    repo_fields: dict[str, list[str]],
    module_fields: list[str],
    coordinator_fields: list[str],
) -> int:
    available_modules = discover_module_repos(config)
    overrides = parse_version_overrides(args.versions, config, available_modules)
    entries = plan_versions(config, plan, None, overrides)
    print_version_plan(entries)

    if not any(entry.released for entry in entries):
        print("Nothing to release: no selected repo has releasable commits since its last tag.")
        return 0

    if args.pin_coordinator_module_deps:
        pins = build_coordinator_version_pins(config, entries, available_modules)
        pin_field = build_dependency_pins_field(pins)
        if pin_field is not None:
            coordinator_fields.append(pin_field)
    if args.verify_ci:
        verify_version_plan_ci(config, entries, child_dry_run)
    if not args.plan_only:
        dispatch_version_plan(config, entries, child_dry_run, repo_fields, module_fields, coordinator_fields)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = ReleaseConfig(
        org=args.org,
        team=args.team,
        coordinator_repo=args.coordinator_repo,
        dependency_repo=args.dependency_repo,
        dependency_org=args.dependency_org,
        root=Path(args.root).resolve(),
        workflow=args.workflow,
        branch=args.branch,
    )
    child_dry_run = parse_bool(args.child_dry_run)

    try:
        repo_fields = parse_repo_fields(args.repo_field)
        module_fields = parse_workflow_fields(args.module_field)
        coordinator_fields = parse_workflow_fields(args.coordinator_field)
        if args.tag and args.versions.strip():
            raise ReleaseError(
                "Conflicting version inputs",
                "Pass either a lockstep tag or per-repo versions, not both.",
            )
        plan = build_release_plan(config, args.tag, args.targets)
        if args.tag is None:
            return run_computed_release(config, plan, args, child_dry_run, repo_fields, module_fields, coordinator_fields)
        if args.pin_coordinator_module_deps:
            dependency_pin_field = build_coordinator_dependency_pin_field(plan.module_repos, args.tag)
            if dependency_pin_field is not None:
                coordinator_fields.append(dependency_pin_field)
        print_plan(plan, config)
        if args.verify_ci:
            verify_release_plan_ci(config, plan, args.tag, child_dry_run)
        if not args.plan_only:
            dispatch_release_plan(
                config,
                plan,
                args.tag,
                child_dry_run,
                repo_fields,
                module_fields,
                coordinator_fields,
            )
        return 0
    except ReleaseError as exc:
        print(f"::error title={exc.title}::{exc.message}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"::error title=GitHub CLI failed::gh exited with {exc.returncode}", file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
