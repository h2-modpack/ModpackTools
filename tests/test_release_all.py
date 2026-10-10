#!/usr/bin/env python3
"""Dry-run tests for ModpackTools/github/release_all.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TEST_DIR.parent

sys.path.insert(0, str(TOOLS_DIR / "github"))
import release_all  # noqa: E402


MODULE_REPOS = [
    "adamantRunDirector-BiomeControl",
    "adamantRunDirector-BoonBans",
    "adamantRunDirector-GodPool",
]


def make_config() -> release_all.ReleaseConfig:
    return release_all.ReleaseConfig(
        org="h2pack-rundirector",
        team="adamantRunDirector",
        coordinator_repo="adamantRunDirector-RunDirector_Modpack",
        root=Path("unused"),
    )


def make_config_with_dependency() -> release_all.ReleaseConfig:
    return release_all.ReleaseConfig(
        org="h2pack-rundirector",
        team="adamantRunDirector",
        coordinator_repo="adamantRunDirector-RunDirector_Modpack",
        dependency_org="h2-modpack",
        dependency_repo="adamant-ModpackLib",
        root=Path("unused"),
    )


def build_plan(
    tag: str,
    targets: str | None,
    config: release_all.ReleaseConfig | None = None,
) -> release_all.ReleasePlan:
    return release_all.build_release_plan(config or make_config(), tag, targets, MODULE_REPOS)


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(value, label: str) -> None:
    if not value:
        raise AssertionError(f"{label}: expected truthy value")


def assert_raises(title: str, func) -> release_all.ReleaseError:
    try:
        func()
    except release_all.ReleaseError as exc:
        assert_equal(exc.title, title, "error title")
        return exc
    raise AssertionError(f"expected ReleaseError titled {title!r}")


def test_mass_release_selects_all_modules_and_coordinator() -> None:
    plan = build_plan("1.2.0", "")
    assert_equal(plan.module_repos, MODULE_REPOS, "mass modules")
    assert_true(plan.coordinator_selected, "mass coordinator selected")


def test_mass_release_ignores_deprecated_dependency_config() -> None:
    plan = build_plan("1.2.0", "", make_config_with_dependency())
    assert_equal(plan.module_repos, MODULE_REPOS, "mass modules")
    assert_true(plan.coordinator_selected, "mass coordinator selected")


def test_targeted_release_accepts_module_and_coordinator_aliases() -> None:
    plan = build_plan(
        "1.2.1",
        "BiomeControl, adamantRunDirector-GodPool, BoonBans, Coordinator, Modpack, RunDirector_Modpack",
    )
    assert_equal(
        plan.module_repos,
        [
            "adamantRunDirector-BiomeControl",
            "adamantRunDirector-GodPool",
            "adamantRunDirector-BoonBans",
        ],
        "targeted modules",
    )
    assert_true(plan.coordinator_selected, "targeted coordinator selected")


def test_targeted_release_rejects_dependency_aliases() -> None:
    exc = assert_raises(
        "Unsupported release target",
        lambda: build_plan("1.2.1", "Lib", make_config_with_dependency()),
    )
    if "Release Lib from its own repository" not in exc.message:
        raise AssertionError(f"unexpected dependency target message: {exc.message}")


def test_targeted_dependency_zero_patch_reports_dependency_boundary() -> None:
    assert_raises(
        "Unsupported release target",
        lambda: build_plan("3.0.0", "Lib", make_config_with_dependency()),
    )


def test_targeted_release_deduplicates_modules() -> None:
    plan = build_plan(
        "1.2.1",
        "BiomeControl, adamantRunDirector-BiomeControl",
    )
    assert_equal(plan.module_repos, ["adamantRunDirector-BiomeControl"], "deduped modules")


def test_old_prefixed_module_target_is_rejected() -> None:
    exc = assert_raises(
        "Unknown release target",
        lambda: build_plan("1.2.1", "RunDirector_GodPool"),
    )
    if "adamantRunDirector-RunDirector_GodPool" not in exc.message:
        raise AssertionError(f"unexpected unknown target message: {exc.message}")


def test_unknown_target_reports_current_repo_name() -> None:
    exc = assert_raises(
        "Unknown release target",
        lambda: build_plan("1.2.1", "MissingThing"),
    )
    if "adamantRunDirector-MissingThing" not in exc.message:
        raise AssertionError(f"unexpected unknown target message: {exc.message}")


def test_mass_release_requires_zero_patch() -> None:
    assert_raises("Invalid Mass Release Version", lambda: build_plan("1.2.1", ""))


def test_targeted_release_requires_nonzero_patch() -> None:
    assert_raises("Invalid Hotfix Version", lambda: build_plan("1.2.0", "BiomeControl"))


def test_empty_target_list_is_rejected() -> None:
    assert_raises("No release targets", lambda: build_plan("1.2.1", " , , "))


def test_dispatch_fields_include_generic_repo_fields() -> None:
    fields = release_all.build_dispatch_fields(
        "1.2.1",
        True,
        ["include-private-padding=true"],
    )
    assert_equal(fields, [
        "tag=1.2.1",
        "is-dry-run=true",
        "include-private-padding=true",
    ], "dispatch fields")


def test_parse_repo_fields_groups_fields_by_repo() -> None:
    fields = release_all.parse_repo_fields(
        [
            "adamantRunDirector-BoonBans:include-private-padding=true",
            "adamantRunDirector-GodPool:custom=value",
        ]
    )
    assert_equal(fields, {
        "adamantRunDirector-BoonBans": ["include-private-padding=true"],
        "adamantRunDirector-GodPool": ["custom=value"],
    }, "repo fields")


def test_parse_workflow_fields_accepts_key_value_pairs() -> None:
    fields = release_all.parse_workflow_fields(
        [
            "lib-version=3.0.0",
            " dependency-pins=adamantRunDirector-GodPool=3.0.0 ",
        ]
    )
    assert_equal(fields, [
        "lib-version=3.0.0",
        "dependency-pins=adamantRunDirector-GodPool=3.0.0",
    ], "workflow fields")


def test_parse_workflow_fields_rejects_missing_value_separator() -> None:
    assert_raises("Invalid workflow field", lambda: release_all.parse_workflow_fields(["lib-version"]))


def test_build_coordinator_dependency_pin_field_uses_selected_module_repos() -> None:
    field = release_all.build_coordinator_dependency_pin_field(
        [
            "adamantRunDirector-BiomeControl",
            "adamantRunDirector-GodPool",
        ],
        "3.0.0",
    )
    assert_equal(
        field,
        "dependency-pins=adamantRunDirector-BiomeControl=3.0.0,adamantRunDirector-GodPool=3.0.0",
        "coordinator dependency pins",
    )


def test_merge_workflow_fields_combines_shared_and_repo_specific_fields() -> None:
    fields = release_all.merge_workflow_fields(
        "adamantRunDirector-BoonBans",
        ["lib-version=3.0.0"],
        {"adamantRunDirector-BoonBans": ["include-private-padding=true"]},
    )
    assert_equal(fields, [
        "lib-version=3.0.0",
        "include-private-padding=true",
    ], "merged workflow fields")


def test_has_workflow_field_matches_field_name() -> None:
    assert_true(
        release_all.has_workflow_field(["lib-version=3.0.0"], "lib-version"),
        "has lib-version field",
    )
    assert_equal(
        release_all.has_workflow_field(["dependency-pins=Package=3.0.0"], "lib-version"),
        False,
        "missing lib-version field",
    )


def test_release_repos_lists_modules_then_coordinator() -> None:
    config = make_config()
    plan = release_all.ReleasePlan(
        module_repos=[
            "adamantRunDirector-BiomeControl",
            "adamantRunDirector-GodPool",
        ],
        coordinator_selected=True,
    )
    assert_equal(
        release_all.release_repos(config, plan),
        [
            "adamantRunDirector-BiomeControl",
            "adamantRunDirector-GodPool",
            "adamantRunDirector-RunDirector_Modpack",
        ],
        "release repos",
    )


def test_verify_release_plan_ci_checks_selected_repos_and_skips_existing() -> None:
    config = make_config()
    plan = release_all.ReleasePlan(
        module_repos=[
            "adamantRunDirector-BiomeControl",
            "adamantRunDirector-BoonBans",
        ],
        coordinator_selected=True,
    )
    calls: list[tuple[str, str]] = []
    shas = {
        "adamantRunDirector-BiomeControl": "a" * 40,
        "adamantRunDirector-RunDirector_Modpack": "b" * 40,
    }
    old_release_exists = release_all.release_exists
    old_local_repo_head = release_all.local_repo_head
    old_remote_branch_head = release_all.remote_branch_head
    old_successful_ci_run_for_commit = release_all.successful_ci_run_for_commit

    def fake_release_exists(_config, repo, _tag):
        calls.append(("exists", repo))
        return repo == "adamantRunDirector-BoonBans"

    def fake_local_repo_head(_config, repo):
        calls.append(("local", repo))
        return shas[repo]

    def fake_remote_branch_head(_config, repo):
        calls.append(("remote", repo))
        return shas[repo]

    def fake_successful_ci_run_for_commit(_config, repo, sha):
        calls.append(("ci", repo))
        return 1000 + len(sha)

    try:
        release_all.release_exists = fake_release_exists
        release_all.local_repo_head = fake_local_repo_head
        release_all.remote_branch_head = fake_remote_branch_head
        release_all.successful_ci_run_for_commit = fake_successful_ci_run_for_commit

        release_all.verify_release_plan_ci(config, plan, "1.2.0", False)
        assert_equal(calls, [
            ("exists", "adamantRunDirector-BiomeControl"),
            ("local", "adamantRunDirector-BiomeControl"),
            ("remote", "adamantRunDirector-BiomeControl"),
            ("ci", "adamantRunDirector-BiomeControl"),
            ("exists", "adamantRunDirector-BoonBans"),
            ("exists", "adamantRunDirector-RunDirector_Modpack"),
            ("local", "adamantRunDirector-RunDirector_Modpack"),
            ("remote", "adamantRunDirector-RunDirector_Modpack"),
            ("ci", "adamantRunDirector-RunDirector_Modpack"),
        ], "ci preflight calls")
    finally:
        release_all.release_exists = old_release_exists
        release_all.local_repo_head = old_local_repo_head
        release_all.remote_branch_head = old_remote_branch_head
        release_all.successful_ci_run_for_commit = old_successful_ci_run_for_commit


def test_verify_repo_ci_rejects_release_ref_mismatch() -> None:
    config = make_config()
    old_release_exists = release_all.release_exists
    old_local_repo_head = release_all.local_repo_head
    old_remote_branch_head = release_all.remote_branch_head

    try:
        release_all.release_exists = lambda _config, _repo, _tag: False
        release_all.local_repo_head = lambda _config, _repo: "a" * 40
        release_all.remote_branch_head = lambda _config, _repo: "b" * 40

        assert_raises(
            "Release ref mismatch",
            lambda: release_all.verify_repo_ci(
                config,
                "adamantRunDirector-BiomeControl",
                "1.2.0",
                False,
            ),
        )
    finally:
        release_all.release_exists = old_release_exists
        release_all.local_repo_head = old_local_repo_head
        release_all.remote_branch_head = old_remote_branch_head


def test_verify_repo_ci_rejects_missing_successful_ci() -> None:
    config = make_config()
    old_release_exists = release_all.release_exists
    old_local_repo_head = release_all.local_repo_head
    old_remote_branch_head = release_all.remote_branch_head
    old_successful_ci_run_for_commit = release_all.successful_ci_run_for_commit

    try:
        release_all.release_exists = lambda _config, _repo, _tag: False
        release_all.local_repo_head = lambda _config, _repo: "a" * 40
        release_all.remote_branch_head = lambda _config, _repo: "a" * 40
        release_all.successful_ci_run_for_commit = lambda _config, _repo, _sha: None

        assert_raises(
            "CI has not passed",
            lambda: release_all.verify_repo_ci(
                config,
                "adamantRunDirector-BiomeControl",
                "1.2.0",
                False,
            ),
        )
    finally:
        release_all.release_exists = old_release_exists
        release_all.local_repo_head = old_local_repo_head
        release_all.remote_branch_head = old_remote_branch_head
        release_all.successful_ci_run_for_commit = old_successful_ci_run_for_commit


def test_dispatch_repo_uses_configured_branch_ref() -> None:
    config = make_config()
    calls: list[list[str]] = []
    old_list_release_runs = release_all.list_release_runs
    old_run_gh = release_all.run_gh
    old_find_new_release_run = release_all.find_new_release_run

    def fake_run_gh(args, capture_json=False):
        calls.append(args)
        return [] if capture_json else None

    try:
        release_all.list_release_runs = lambda _config, _repo: []
        release_all.run_gh = fake_run_gh
        release_all.find_new_release_run = lambda *_args: 321

        run_id = release_all.dispatch_repo(
            config,
            "adamantRunDirector-BiomeControl",
            "1.2.0",
            True,
        )
        assert_equal(run_id, 321, "dispatch run id")
        assert_equal(calls[0], [
            "workflow",
            "run",
            "release.yaml",
            "--repo",
            "h2pack-rundirector/adamantRunDirector-BiomeControl",
            "--ref",
            "main",
            "--field",
            "tag=1.2.0",
            "--field",
            "is-dry-run=true",
        ], "workflow dispatch command")
    finally:
        release_all.list_release_runs = old_list_release_runs
        release_all.run_gh = old_run_gh
        release_all.find_new_release_run = old_find_new_release_run


def test_dispatch_plan_releases_modules_and_coordinator_only() -> None:
    config = make_config_with_dependency()
    plan = build_plan("1.2.0", "", config)
    calls: list[tuple[str, str, tuple[str, ...]]] = []
    old_release_exists = release_all.release_exists
    old_dispatch_repo = release_all.dispatch_repo
    old_watch_repo = release_all.watch_repo

    def fake_release_exists(_config, _repo, _tag):
        return False

    def fake_dispatch_repo(_config, repo, _tag, _child_dry_run, _repo_fields, shared_fields):
        calls.append((_config.org, repo, tuple(shared_fields or [])))
        return len(calls)

    def fake_watch_repo(_config, _repo, _run_id):
        return None

    try:
        release_all.release_exists = fake_release_exists
        release_all.dispatch_repo = fake_dispatch_repo
        release_all.watch_repo = fake_watch_repo

        module_fields = []
        coordinator_fields = []
        module_fields.append("lib-version=3.0.0")
        coordinator_fields.append("lib-version=3.0.0")
        release_all.dispatch_release_plan(
            config,
            plan,
            "1.2.0",
            True,
            module_fields=module_fields,
            coordinator_fields=coordinator_fields,
        )
        assert_equal(calls[0], ("h2pack-rundirector", MODULE_REPOS[0], ("lib-version=3.0.0",)), "first module dispatch")
        assert_equal(calls[-1], (
            "h2pack-rundirector",
            "adamantRunDirector-RunDirector_Modpack",
            ("lib-version=3.0.0",),
        ), "coordinator dispatch")
    finally:
        release_all.release_exists = old_release_exists
        release_all.dispatch_repo = old_dispatch_repo
        release_all.watch_repo = old_watch_repo


def test_release_phase_waits_for_each_repo_before_dispatching_next() -> None:
    config = make_config()
    calls: list[tuple[str, str]] = []
    old_release_exists = release_all.release_exists
    old_dispatch_repo = release_all.dispatch_repo
    old_watch_repo = release_all.watch_repo

    def fake_release_exists(_config, repo, _tag):
        calls.append(("exists", repo))
        return False

    def fake_dispatch_repo(_config, repo, _tag, _child_dry_run, _repo_fields, _shared_fields):
        calls.append(("dispatch", repo))
        return 100 + len(calls)

    def fake_watch_repo(_config, repo, _run_id):
        calls.append(("watch", repo))
        if repo == "adamantRunDirector-BoonBans":
            raise subprocess.CalledProcessError(1, ["gh", "run", "watch"])

    try:
        release_all.release_exists = fake_release_exists
        release_all.dispatch_repo = fake_dispatch_repo
        release_all.watch_repo = fake_watch_repo

        exc = assert_raises(
            "Release failed",
            lambda: release_all.release_phase(
                config,
                "Module releases",
                MODULE_REPOS,
                "1.2.0",
                False,
            ),
        )
        if "Completed 1 / 3" not in exc.message:
            raise AssertionError(f"unexpected failure message: {exc.message}")
        assert_equal(calls, [
            ("exists", "adamantRunDirector-BiomeControl"),
            ("dispatch", "adamantRunDirector-BiomeControl"),
            ("watch", "adamantRunDirector-BiomeControl"),
            ("exists", "adamantRunDirector-BoonBans"),
            ("dispatch", "adamantRunDirector-BoonBans"),
            ("watch", "adamantRunDirector-BoonBans"),
        ], "sequential release calls")
    finally:
        release_all.release_exists = old_release_exists
        release_all.dispatch_repo = old_dispatch_repo
        release_all.watch_repo = old_watch_repo


def test_release_phase_skips_existing_releases() -> None:
    config = make_config()
    calls: list[tuple[str, str]] = []
    old_release_exists = release_all.release_exists
    old_dispatch_repo = release_all.dispatch_repo
    old_watch_repo = release_all.watch_repo

    def fake_release_exists(_config, repo, _tag):
        calls.append(("exists", repo))
        return repo == "adamantRunDirector-BoonBans"

    def fake_dispatch_repo(_config, repo, _tag, _child_dry_run, _repo_fields, _shared_fields):
        calls.append(("dispatch", repo))
        return 200 + len(calls)

    def fake_watch_repo(_config, repo, _run_id):
        calls.append(("watch", repo))

    try:
        release_all.release_exists = fake_release_exists
        release_all.dispatch_repo = fake_dispatch_repo
        release_all.watch_repo = fake_watch_repo

        succeeded = release_all.release_phase(
            config,
            "Module releases",
            MODULE_REPOS,
            "1.2.0",
            False,
        )
        assert_equal(succeeded, 3, "release success count")
        assert_equal(calls, [
            ("exists", "adamantRunDirector-BiomeControl"),
            ("dispatch", "adamantRunDirector-BiomeControl"),
            ("watch", "adamantRunDirector-BiomeControl"),
            ("exists", "adamantRunDirector-BoonBans"),
            ("exists", "adamantRunDirector-GodPool"),
            ("dispatch", "adamantRunDirector-GodPool"),
            ("watch", "adamantRunDirector-GodPool"),
        ], "skip existing release calls")
    finally:
        release_all.release_exists = old_release_exists
        release_all.dispatch_repo = old_dispatch_repo
        release_all.watch_repo = old_watch_repo


COORDINATOR = "adamantRunDirector-RunDirector_Modpack"


class patched:
    """Temporarily replace module attributes."""

    def __init__(self, target, **replacements):
        self.target = target
        self.replacements = replacements
        self.originals = {}

    def __enter__(self):
        for name, value in self.replacements.items():
            self.originals[name] = getattr(self.target, name)
            setattr(self.target, name, value)

    def __exit__(self, *_):
        for name, value in self.originals.items():
            setattr(self.target, name, value)


def fake_state(states):
    def read(_config, repo):
        return states[repo]

    return read


def test_plan_versions_computes_per_repo_versions_and_skips_unchanged() -> None:
    config = make_config()
    plan = release_all.ReleasePlan(module_repos=list(MODULE_REPOS), coordinator_selected=True)
    states = {
        MODULE_REPOS[0]: ("2.1.0", 3, "minor"),
        MODULE_REPOS[1]: ("1.4.2", 0, None),
        MODULE_REPOS[2]: ("5.0.0", 1, "patch"),
        COORDINATOR: ("3.0.0", 2, None),
    }
    with patched(release_all, repo_release_state=fake_state(states)):
        entries = release_all.plan_versions(config, plan)
    by_repo = {entry.repo: entry for entry in entries}

    assert_equal(by_repo[MODULE_REPOS[0]].version, "2.2.0", "feat bumps minor")
    assert_equal(by_repo[MODULE_REPOS[1]].version, None, "unchanged module skipped")
    assert_equal(by_repo[MODULE_REPOS[2]].version, "5.0.1", "fix bumps patch")
    assert_equal(by_repo[COORDINATOR].version, "3.1.0", "coordinator takes the largest module bump")
    assert_equal(by_repo[COORDINATOR].source, "modules", "coordinator source")


def test_plan_versions_coordinator_gets_at_least_a_patch() -> None:
    config = make_config()
    plan = release_all.ReleasePlan(module_repos=[MODULE_REPOS[0]], coordinator_selected=True)
    states = {
        MODULE_REPOS[0]: ("2.1.0", 1, "patch"),
        COORDINATOR: ("3.0.0", 0, None),
    }
    with patched(release_all, repo_release_state=fake_state(states)):
        entries = release_all.plan_versions(config, plan)
    assert_equal(entries[-1].version, "3.0.1", "coordinator patch")


def test_plan_versions_skips_everything_when_nothing_releasable() -> None:
    config = make_config()
    plan = release_all.ReleasePlan(module_repos=[MODULE_REPOS[0]], coordinator_selected=True)
    states = {
        MODULE_REPOS[0]: ("2.1.0", 0, None),
        COORDINATOR: ("3.0.0", 1, None),
    }
    with patched(release_all, repo_release_state=fake_state(states)):
        entries = release_all.plan_versions(config, plan)
    assert_true(not any(entry.released for entry in entries), "nothing released")


def test_plan_versions_override_wins_and_forces_release() -> None:
    config = make_config()
    plan = release_all.ReleasePlan(module_repos=[MODULE_REPOS[1]], coordinator_selected=True)
    states = {
        MODULE_REPOS[1]: ("1.4.2", 0, None),
        COORDINATOR: ("3.0.0", 0, None),
    }
    overrides = {MODULE_REPOS[1]: "6.0.0", COORDINATOR: "7.0.0"}
    with patched(release_all, repo_release_state=fake_state(states)):
        entries = release_all.plan_versions(config, plan, None, overrides)
    assert_equal([entry.version for entry in entries], ["6.0.0", "7.0.0"], "override versions")
    assert_equal([entry.source for entry in entries], ["override", "override"], "override sources")


def test_plan_versions_lockstep_applies_one_tag_to_every_selected_repo() -> None:
    config = make_config()
    plan = release_all.ReleasePlan(module_repos=[MODULE_REPOS[0], MODULE_REPOS[1]], coordinator_selected=True)
    states = {
        MODULE_REPOS[0]: ("2.1.0", 1, "patch"),
        MODULE_REPOS[1]: ("1.4.2", 0, None),
        COORDINATOR: ("3.0.0", 0, None),
    }
    with patched(release_all, repo_release_state=fake_state(states)):
        entries = release_all.plan_versions(config, plan, "9.0.0")
    assert_equal([entry.version for entry in entries], ["9.0.0", "9.0.0", "9.0.0"], "lockstep versions")


def test_parse_version_overrides_resolves_aliases_and_validates() -> None:
    config = make_config()
    overrides = release_all.parse_version_overrides(
        "GodPool=2.0.0, Coordinator=4.1.0", config, MODULE_REPOS
    )
    assert_equal(overrides, {MODULE_REPOS[2]: "2.0.0", COORDINATOR: "4.1.0"}, "parsed overrides")
    assert_equal(release_all.parse_version_overrides("", config, MODULE_REPOS), {}, "empty overrides")
    for bad in ("GodPool", "GodPool=2.0", "GodPool=v2.0.0"):
        try:
            release_all.parse_version_overrides(bad, config, MODULE_REPOS)
        except release_all.ReleaseError as exc:
            assert_equal(exc.title, "Invalid version override", f"error title for {bad!r}")
        else:
            raise AssertionError(f"expected invalid override for {bad!r}")


def test_coordinator_version_pins_cover_every_module() -> None:
    config = make_config()
    entries = [
        release_all.VersionPlanEntry(MODULE_REPOS[0], "module", "2.1.0", 3, "minor", "2.2.0", "commits"),
        release_all.VersionPlanEntry(MODULE_REPOS[1], "module", "1.4.2", 0, None, None, "none"),
        release_all.VersionPlanEntry(COORDINATOR, "coordinator", "3.0.0", 0, "minor", "3.1.0", "modules"),
    ]
    latest = {MODULE_REPOS[1]: "1.4.2", MODULE_REPOS[2]: "5.0.0"}
    with patched(release_all.prep, latest_version_tag=lambda path: latest.get(path.name)):
        pins = release_all.build_coordinator_version_pins(config, entries, MODULE_REPOS)
    assert_equal(pins, {
        MODULE_REPOS[0]: "2.2.0",
        MODULE_REPOS[1]: "1.4.2",
        MODULE_REPOS[2]: "5.0.0",
    }, "pins")
    assert_equal(
        release_all.build_dependency_pins_field(pins),
        "dependency-pins="
        f"{MODULE_REPOS[0]}=2.2.0,{MODULE_REPOS[1]}=1.4.2,{MODULE_REPOS[2]}=5.0.0",
        "pin field",
    )
    assert_equal(release_all.build_dependency_pins_field({}), None, "empty pin field")


def test_format_version_plan_marks_skips_and_overrides() -> None:
    entries = [
        release_all.VersionPlanEntry(MODULE_REPOS[0], "module", "2.1.0", 3, "minor", "2.2.0", "commits"),
        release_all.VersionPlanEntry(MODULE_REPOS[1], "module", "1.4.2", 0, None, None, "none"),
        release_all.VersionPlanEntry(MODULE_REPOS[2], "module", None, 4, None, "6.0.0", "override"),
    ]
    text = release_all.format_version_plan(entries)
    assert_true("2.2.0" in text, "next version shown")
    assert_true("skip (none)" in text, "skip shown")
    assert_true("6.0.0 (override)" in text, "override shown")


def test_dispatch_version_plan_uses_each_repos_own_version_and_skips_unreleased() -> None:
    config = make_config()
    entries = [
        release_all.VersionPlanEntry(MODULE_REPOS[0], "module", "2.1.0", 3, "minor", "2.2.0", "commits"),
        release_all.VersionPlanEntry(MODULE_REPOS[1], "module", "1.4.2", 0, None, None, "none"),
        release_all.VersionPlanEntry(COORDINATOR, "coordinator", "3.0.0", 0, "minor", "3.1.0", "modules"),
    ]
    dispatched: list[tuple[str, str, tuple[str, ...]]] = []

    def fake_dispatch_repo(_config, repo, tag, _dry, _repo_fields, shared_fields):
        dispatched.append((repo, tag, tuple(shared_fields or [])))
        return len(dispatched)

    with patched(
        release_all,
        release_exists=lambda *_: False,
        dispatch_repo=fake_dispatch_repo,
        watch_repo=lambda *_: None,
    ):
        release_all.dispatch_version_plan(
            config,
            entries,
            False,
            module_fields=["tools-ref=v"],
            coordinator_fields=["dependency-pins=x=1.0.0"],
        )
    assert_equal(dispatched, [
        (MODULE_REPOS[0], "2.2.0", ("tools-ref=v",)),
        (COORDINATOR, "3.1.0", ("dependency-pins=x=1.0.0",)),
    ], "dispatches")


def test_verify_version_plan_ci_checks_only_released_repos() -> None:
    config = make_config()
    entries = [
        release_all.VersionPlanEntry(MODULE_REPOS[0], "module", "2.1.0", 3, "minor", "2.2.0", "commits"),
        release_all.VersionPlanEntry(MODULE_REPOS[1], "module", "1.4.2", 0, None, None, "none"),
    ]
    checked: list[tuple[str, str]] = []
    with patched(release_all, verify_repo_ci=lambda _c, repo, tag, _d: checked.append((repo, tag))):
        release_all.verify_version_plan_ci(config, entries, False)
    assert_equal(checked, [(MODULE_REPOS[0], "2.2.0")], "ci checks")


def test_main_rejects_tag_combined_with_versions() -> None:
    code = release_all.main([
        "--org", "o", "--team", "adamantRunDirector", "--coordinator-repo", COORDINATOR,
        "--tag", "1.0.0", "--versions", "GodPool=2.0.0", "--plan-only",
    ])
    assert_equal(code, 1, "exit code")


def test_run_computed_release_does_nothing_when_nothing_is_releasable() -> None:
    config = make_config()
    plan = release_all.ReleasePlan(module_repos=[MODULE_REPOS[0]], coordinator_selected=False)
    args = release_all.build_parser().parse_args([
        "--org", "o", "--team", "adamantRunDirector", "--coordinator-repo", COORDINATOR,
    ])
    states = {MODULE_REPOS[0]: ("2.1.0", 0, None)}

    def boom(*_args, **_kwargs):
        raise AssertionError("must not dispatch")

    with patched(
        release_all,
        repo_release_state=fake_state(states),
        discover_module_repos=lambda _config: list(MODULE_REPOS),
        dispatch_version_plan=boom,
    ):
        code = release_all.run_computed_release(config, plan, args, False, {}, [], [])
    assert_equal(code, 0, "exit code")


def raises_release_error(title, func) -> None:
    try:
        func()
    except release_all.ReleaseError as exc:
        assert_equal(exc.title, title, "error title")
        return
    raise AssertionError(f"expected ReleaseError titled {title!r}")


def test_repo_release_state_rejects_shallow_checkout() -> None:
    config = make_config()
    with patched(release_all.prep, is_shallow=lambda _path: True):
        raises_release_error("Shallow release checkout", lambda: release_all.repo_release_state(config, MODULE_REPOS[0]))


def test_repo_release_state_rejects_unreachable_latest_tag() -> None:
    config = make_config()
    commit = release_all.prep.Commit("a" * 40, "fix: x", "")
    with patched(
        release_all.prep,
        is_shallow=lambda _path: False,
        commits_since_latest_tag=lambda _path: (None, [commit]),
        all_version_tags=lambda _path: ["5.2.1", "5.0.0"],
    ):
        raises_release_error(
            "Incomplete release history",
            lambda: release_all.repo_release_state(config, MODULE_REPOS[0]),
        )


def test_repo_release_state_accepts_complete_history_and_new_repos() -> None:
    config = make_config()
    commit = release_all.prep.Commit("a" * 40, "feat: x", "")
    with patched(
        release_all.prep,
        is_shallow=lambda _path: False,
        commits_since_latest_tag=lambda _path: ("5.2.1", [commit]),
        all_version_tags=lambda _path: ["5.2.1", "5.0.0"],
    ):
        assert_equal(release_all.repo_release_state(config, MODULE_REPOS[0]), ("5.2.1", 1, "minor"), "complete history")
    with patched(
        release_all.prep,
        is_shallow=lambda _path: False,
        commits_since_latest_tag=lambda _path: (None, [commit]),
        all_version_tags=lambda _path: [],
    ):
        assert_equal(release_all.repo_release_state(config, MODULE_REPOS[0]), (None, 1, "minor"), "untagged repo")


def test_plan_versions_rejects_override_not_above_last_tag() -> None:
    config = make_config()
    plan = release_all.ReleasePlan(module_repos=[MODULE_REPOS[0]], coordinator_selected=False)
    states = {MODULE_REPOS[0]: ("5.2.1", 1, "patch")}
    with patched(release_all, repo_release_state=fake_state(states)):
        for version in ("5.2.1", "5.0.0", "1.0.0"):
            raises_release_error(
                "Version not above last release",
                lambda version=version: release_all.plan_versions(
                    config, plan, None, {MODULE_REPOS[0]: version}
                ),
            )
        entries = release_all.plan_versions(config, plan, None, {MODULE_REPOS[0]: "5.2.2"})
    assert_equal(entries[0].version, "5.2.2", "override above last tag")


def main() -> int:
    tests = [
        test_mass_release_selects_all_modules_and_coordinator,
        test_mass_release_ignores_deprecated_dependency_config,
        test_targeted_release_accepts_module_and_coordinator_aliases,
        test_targeted_release_rejects_dependency_aliases,
        test_targeted_dependency_zero_patch_reports_dependency_boundary,
        test_targeted_release_deduplicates_modules,
        test_old_prefixed_module_target_is_rejected,
        test_unknown_target_reports_current_repo_name,
        test_mass_release_requires_zero_patch,
        test_targeted_release_requires_nonzero_patch,
        test_empty_target_list_is_rejected,
        test_dispatch_fields_include_generic_repo_fields,
        test_parse_repo_fields_groups_fields_by_repo,
        test_parse_workflow_fields_accepts_key_value_pairs,
        test_parse_workflow_fields_rejects_missing_value_separator,
        test_build_coordinator_dependency_pin_field_uses_selected_module_repos,
        test_merge_workflow_fields_combines_shared_and_repo_specific_fields,
        test_has_workflow_field_matches_field_name,
        test_release_repos_lists_modules_then_coordinator,
        test_verify_release_plan_ci_checks_selected_repos_and_skips_existing,
        test_verify_repo_ci_rejects_release_ref_mismatch,
        test_verify_repo_ci_rejects_missing_successful_ci,
        test_dispatch_repo_uses_configured_branch_ref,
        test_dispatch_plan_releases_modules_and_coordinator_only,
        test_release_phase_waits_for_each_repo_before_dispatching_next,
        test_release_phase_skips_existing_releases,
        test_plan_versions_computes_per_repo_versions_and_skips_unchanged,
        test_plan_versions_coordinator_gets_at_least_a_patch,
        test_plan_versions_skips_everything_when_nothing_releasable,
        test_plan_versions_override_wins_and_forces_release,
        test_plan_versions_lockstep_applies_one_tag_to_every_selected_repo,
        test_parse_version_overrides_resolves_aliases_and_validates,
        test_coordinator_version_pins_cover_every_module,
        test_format_version_plan_marks_skips_and_overrides,
        test_dispatch_version_plan_uses_each_repos_own_version_and_skips_unreleased,
        test_verify_version_plan_ci_checks_only_released_repos,
        test_main_rejects_tag_combined_with_versions,
        test_run_computed_release_does_nothing_when_nothing_is_releasable,
        test_repo_release_state_rejects_shallow_checkout,
        test_repo_release_state_rejects_unreachable_latest_tag,
        test_repo_release_state_accepts_complete_history_and_new_repos,
        test_plan_versions_rejects_override_not_above_last_tag,
    ]

    for test in tests:
        test()
    print(f"{len(tests)} release_all dry-run tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
