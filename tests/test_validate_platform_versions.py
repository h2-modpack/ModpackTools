#!/usr/bin/env python3
"""Tests for validate_platform_versions.py with and without pack.toml."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import validate_platform_versions as validator  # noqa: E402


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def write_package(path: Path, namespace: str, name: str, dependencies: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    deps = "".join(f'{key} = "{value}"\n' for key, value in dependencies.items())
    path.write_text(
        f'[package]\nnamespace = "{namespace}"\nname = "{name}"\nversionNumber = "1.0.0"\n\n'
        f"[package.dependencies]\n{deps}",
        encoding="utf-8",
    )


def make_shell(coordinator_modules: list[str], registered: list[str], pack_toml: str | None) -> Path:
    root = Path(tempfile.mkdtemp())
    write_package(root / "adamant-ModpackLib" / "thunderstore.toml", "adamant", "ModpackLib", {})
    write_package(
        root / "adamantSpeedrun-Speedrun_Modpack" / "thunderstore.toml",
        "adamantSpeedrun",
        "Speedrun_Modpack",
        {"adamant-ModpackLib": "1.0.0", **{f"adamantSpeedrun-{name}": "1.0.0" for name in coordinator_modules}},
    )
    gitmodules = ""
    for name in registered:
        write_package(
            root / "Submodules" / f"adamantSpeedrun-{name}" / "thunderstore.toml",
            "adamantSpeedrun",
            name,
            {"adamant-ModpackLib": "1.0.0"},
        )
        gitmodules += (
            f'[submodule "Submodules/adamantSpeedrun-{name}"]\n'
            f"\tpath = Submodules/adamantSpeedrun-{name}\n\turl = https://example.invalid/{name}.git\n"
        )
    (root / ".gitmodules").write_text(gitmodules, encoding="utf-8")
    if pack_toml is not None:
        (root / "pack.toml").write_text(pack_toml, encoding="utf-8")
    return root


def run_validator(root: Path) -> tuple[int, str]:
    old_root = validator.ROOT
    buffer = io.StringIO()
    try:
        validator.ROOT = root
        with contextlib.redirect_stdout(buffer):
            code = validator.main()
    finally:
        validator.ROOT = old_root
    return code, buffer.getvalue()


def test_every_module_must_be_a_dependency_without_pack_toml() -> None:
    code, output = run_validator(make_shell(["Alpha"], ["Alpha", "Beta"], None))
    assert_equal(code, 1, "exit code")
    assert_equal("missing dependency adamantSpeedrun-Beta" in output, True, "Beta must be a dependency")


def test_standalone_module_needs_no_coordinator_dependency() -> None:
    root = make_shell(["Alpha"], ["Alpha", "Beta"], '[dependencies]\nmodules = ["Alpha"]\n')
    code, output = run_validator(root)
    assert_equal(code, 0, "exit code")
    assert_equal("Standalone modules" in output, True, "standalone modules are listed")
    assert_equal("adamantSpeedrun-Beta" in output, True, "standalone module is named")


def test_standalone_module_still_needs_the_lib_dependency() -> None:
    root = make_shell(["Alpha"], ["Alpha", "Beta"], '[dependencies]\nmodules = ["Alpha"]\n')
    write_package(root / "Submodules" / "adamantSpeedrun-Beta" / "thunderstore.toml", "adamantSpeedrun", "Beta", {})
    code, output = run_validator(root)
    assert_equal(code, 1, "exit code")
    assert_equal("missing dependency adamant-ModpackLib" in output, True, "Lib edge is still required")


def test_coordinator_depending_on_a_standalone_module_is_an_error() -> None:
    root = make_shell(["Alpha", "Beta"], ["Alpha", "Beta"], '[dependencies]\nmodules = ["Alpha"]\n')
    code, output = run_validator(root)
    assert_equal(code, 1, "exit code")
    assert_equal("depends on standalone module adamantSpeedrun-Beta" in output, True, "leftover dependency")


def test_pack_toml_naming_an_unregistered_module_is_an_error() -> None:
    code, output = run_validator(make_shell(["Alpha"], ["Alpha"], '[dependencies]\nmodules = ["Alpha", "Ghost"]\n'))
    assert_equal(code, 1, "exit code")
    assert_equal("Ghost" in output, True, "error names the module")


def test_unregistered_local_folders_are_ignored() -> None:
    root = make_shell(["Alpha"], ["Alpha"], '[dependencies]\nmodules = ["Alpha"]\n')
    write_package(root / "Submodules" / "adamantSpeedrun-Scratch" / "thunderstore.toml", "adamantSpeedrun", "Scratch", {})
    code, _ = run_validator(root)
    assert_equal(code, 0, "an unregistered folder under Submodules/ is not validated")


def main() -> int:
    tests = [
        test_every_module_must_be_a_dependency_without_pack_toml,
        test_standalone_module_needs_no_coordinator_dependency,
        test_standalone_module_still_needs_the_lib_dependency,
        test_coordinator_depending_on_a_standalone_module_is_an_error,
        test_pack_toml_naming_an_unregistered_module_is_an_error,
        test_unregistered_local_folders_are_ignored,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} validate_platform_versions tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
