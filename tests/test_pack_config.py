#!/usr/bin/env python3
"""Tests for pack.toml handling in new_module/module_roster.py."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR / "new_module"))

import module_roster  # noqa: E402


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_raises_pack_error(func, fragment: str) -> None:
    try:
        func()
    except module_roster.PackConfigError as exc:
        if fragment not in str(exc):
            raise AssertionError(f"error {str(exc)!r} does not mention {fragment!r}") from exc
        return
    raise AssertionError(f"expected PackConfigError mentioning {fragment!r}")


def make_root(modules: list[str], pack_toml: str | None = None) -> Path:
    root = Path(tempfile.mkdtemp())
    gitmodules = ""
    for name in modules:
        folder = root / "Submodules" / f"adamantSpeedrun-{name}"
        folder.mkdir(parents=True)
        (folder / "thunderstore.toml").write_text(
            f'[package]\nnamespace = "adamantSpeedrun"\nname = "{name}"\nversionNumber = "1.0.0"\n',
            encoding="utf-8",
        )
        gitmodules += (
            f'[submodule "Submodules/adamantSpeedrun-{name}"]\n'
            f"\tpath = Submodules/adamantSpeedrun-{name}\n"
            f"\turl = https://example.invalid/{name}.git\n"
        )
    (root / ".gitmodules").write_text(gitmodules, encoding="utf-8")
    if pack_toml is not None:
        (root / "pack.toml").write_text(pack_toml, encoding="utf-8")
    return root


def ids(repos) -> list[str]:
    return [repo.package_id for repo in repos]


def test_missing_pack_toml_makes_every_module_a_dependency() -> None:
    root = make_root(["Alpha", "Beta"])
    assert_equal(module_roster.read_dependency_modules(root), None, "no pack.toml")
    assert_equal(ids(module_roster.dependency_module_repos(root)), ["Alpha", "Beta"], "dependencies")
    assert_equal(ids(module_roster.standalone_module_repos(root)), [], "standalone")


def test_pack_toml_is_inclusive() -> None:
    root = make_root(["Alpha", "Beta", "Gamma"], '[dependencies]\nmodules = ["Alpha", "Gamma"]\n')
    assert_equal(module_roster.read_dependency_modules(root), ["Alpha", "Gamma"], "listed")
    assert_equal(ids(module_roster.dependency_module_repos(root)), ["Alpha", "Gamma"], "dependencies")
    assert_equal(ids(module_roster.standalone_module_repos(root)), ["Beta"], "standalone")


def test_an_empty_dependency_list_makes_everything_standalone() -> None:
    root = make_root(["Alpha"], "[dependencies]\nmodules = []\n")
    assert_equal(ids(module_roster.dependency_module_repos(root)), [], "dependencies")
    assert_equal(ids(module_roster.standalone_module_repos(root)), ["Alpha"], "standalone")


def test_unknown_module_name_is_an_error() -> None:
    root = make_root(["Alpha"], '[dependencies]\nmodules = ["Alpha", "Ghost"]\n')
    assert_raises_pack_error(lambda: module_roster.dependency_module_repos(root), "Ghost")


def test_malformed_pack_toml_is_an_error() -> None:
    for text in ("[dependencies]\n", '[dependencies]\nmodules = "Alpha"\n', "[dependencies]\nmodules = [1]\n"):
        root = make_root(["Alpha"], text)
        assert_raises_pack_error(lambda root=root: module_roster.read_dependency_modules(root), "list of package ids")


def test_duplicate_entries_are_an_error() -> None:
    root = make_root(["Alpha"], '[dependencies]\nmodules = ["Alpha", "Alpha"]\n')
    assert_raises_pack_error(lambda: module_roster.read_dependency_modules(root), "more than once")


def test_write_then_read_round_trips_sorted() -> None:
    root = make_root(["Alpha", "Beta"])
    module_roster.write_dependency_modules(["Beta", "Alpha", "Beta"], root)
    assert_equal(module_roster.read_dependency_modules(root), ["Alpha", "Beta"], "round trip")
    text = (root / "pack.toml").read_text(encoding="utf-8")
    assert_equal(text.startswith("# Modules that are Thunderstore dependencies"), True, "header comment")


def test_add_dependency_module_creates_pack_toml_from_the_roster() -> None:
    root = make_root(["Alpha", "Beta"])
    assert_equal(module_roster.add_dependency_module("Beta", root), True, "changed")
    assert_equal(module_roster.read_dependency_modules(root), ["Alpha", "Beta"], "created from roster")
    assert_equal(module_roster.add_dependency_module("Beta", root), False, "idempotent")


def test_set_new_module_membership_defaults_to_standalone() -> None:
    root = make_root(["Alpha", "Beta", "Quick"])
    summary = module_roster.set_new_module_membership("Quick", False, root)
    assert_equal("standalone" in summary, True, "summary")
    assert_equal(module_roster.read_dependency_modules(root), ["Alpha", "Beta"], "others become the list")

    again = module_roster.set_new_module_membership("Quick", False, root)
    assert_equal("--dependency" in again, True, "second call explains the flag")
    assert_equal(module_roster.read_dependency_modules(root), ["Alpha", "Beta"], "unchanged")


def test_set_new_module_membership_can_add_a_dependency() -> None:
    root = make_root(["Alpha", "Beta"], '[dependencies]\nmodules = ["Alpha"]\n')
    summary = module_roster.set_new_module_membership("Beta", True, root)
    assert_equal("dependency" in summary, True, "summary")
    assert_equal(module_roster.read_dependency_modules(root), ["Alpha", "Beta"], "added")


def test_package_id_falls_back_to_the_folder_suffix() -> None:
    repo = module_roster.ModuleRepo(folder_name="adamantSpeedrun-Some_Module", path=Path("x"), package=None)
    assert_equal(repo.package_id, "Some_Module", "package id")


def main() -> int:
    tests = [
        test_missing_pack_toml_makes_every_module_a_dependency,
        test_pack_toml_is_inclusive,
        test_an_empty_dependency_list_makes_everything_standalone,
        test_unknown_module_name_is_an_error,
        test_malformed_pack_toml_is_an_error,
        test_duplicate_entries_are_an_error,
        test_write_then_read_round_trips_sorted,
        test_add_dependency_module_creates_pack_toml_from_the_roster,
        test_set_new_module_membership_defaults_to_standalone,
        test_set_new_module_membership_can_add_a_dependency,
        test_package_id_falls_back_to_the_folder_suffix,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} pack_config tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
