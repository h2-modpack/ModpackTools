#!/usr/bin/env python3
"""Validate modpack platform dependency presence.

Run from a shell repo that has ModpackTools checked out as a submodule. Lib owns
its package version in its checked-out thunderstore.toml file; the shell repo
owns the assembled snapshot through submodule pointers.

Thunderstore resolves dependencies to the latest available matching package, so
this check verifies required dependency edges are present without enforcing exact
source pin equality.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    print("Python 3.11+ is required for tomllib.", file=sys.stderr)
    sys.exit(2)


TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent

sys.path.insert(0, str(TOOLS_DIR / "new_module"))
import module_roster  # noqa: E402


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def load_toml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{rel(path)} does not exist")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def package_name(path: Path, data: dict) -> str:
    package = data.get("package", {})
    namespace = package.get("namespace")
    name = package.get("name")
    if not namespace or not name:
        raise ValueError(f"{rel(path)} is missing package.namespace or package.name")
    return f"{namespace}-{name}"


def package_version(path: Path, data: dict) -> str:
    version = data.get("package", {}).get("versionNumber")
    if not version:
        raise ValueError(f"{rel(path)} is missing package.versionNumber")
    return version


def dependency_version(data: dict, dependency: str) -> str | None:
    return data.get("package", {}).get("dependencies", {}).get(dependency)


def check_dependency(errors: list[str], path: Path, data: dict, dependency: str) -> str | None:
    actual = dependency_version(data, dependency)
    if actual is None:
        errors.append(f"{rel(path)} is missing dependency {dependency}")
        return None
    return actual


def find_coordinator_toml() -> Path:
    for entry in sorted(ROOT.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue

        toml_path = entry / "thunderstore.toml"
        if not toml_path.exists():
            continue

        data = load_toml(toml_path)
        name = data.get("package", {}).get("name", "")
        if name.endswith("_Modpack"):
            return toml_path

    raise FileNotFoundError("No root-level coordinator thunderstore.toml found")


def main() -> int:
    lib_path = ROOT / "adamant-ModpackLib" / "thunderstore.toml"

    try:
        lib_data = load_toml(lib_path)
        coordinator_path = find_coordinator_toml()
        coordinator_data = load_toml(coordinator_path)
        dependency_repos, standalone_repos = module_roster.split_module_repos(
            module_roster.discover_module_repos(ROOT),
            module_roster.read_dependency_modules(ROOT),
        )
        lib_name = package_name(lib_path, lib_data)
        lib_version = package_version(lib_path, lib_data)
    except (OSError, ValueError, module_roster.PackConfigError) as exc:
        print(f"::error::{exc}")
        return 1

    errors: list[str] = []
    dependency_edges: list[tuple[str, str, str]] = []

    def record_dependency(path: Path, data: dict, dependency: str) -> None:
        actual = check_dependency(errors, path, data, dependency)
        if actual is not None:
            dependency_edges.append((rel(path), dependency, actual))

    record_dependency(coordinator_path, coordinator_data, lib_name)

    loaded_modules: list[tuple[str, str]] = []
    standalone_modules: list[tuple[str, str]] = []
    roster = [(repo, True) for repo in dependency_repos] + [(repo, False) for repo in standalone_repos]
    for repo, is_dependency in roster:
        module_path = repo.path / "thunderstore.toml"
        try:
            module_data = load_toml(module_path)
            module_name = package_name(module_path, module_data)
            module_version = package_version(module_path, module_data)
            (loaded_modules if is_dependency else standalone_modules).append((module_name, module_version))
            record_dependency(module_path, module_data, lib_name)
            if is_dependency:
                record_dependency(coordinator_path, coordinator_data, module_name)
            elif dependency_version(coordinator_data, module_name) is not None:
                errors.append(
                    f"{rel(coordinator_path)} depends on standalone module {module_name}; "
                    "list it in pack.toml or remove the dependency"
                )
        except (OSError, ValueError) as exc:
            errors.append(str(exc))

    print("Platform version snapshot:")
    print(f"  {lib_name} {lib_version}")
    print(f"  {package_name(coordinator_path, coordinator_data)} {package_version(coordinator_path, coordinator_data)}")
    print("  Modules (coordinator dependencies):")
    if loaded_modules:
        for module_name, module_version in loaded_modules:
            print(f"    {module_name} {module_version}")
    else:
        print("    (none)")
    if standalone_modules:
        print("  Standalone modules (ship with the pack, not dependencies):")
        for module_name, module_version in standalone_modules:
            print(f"    {module_name} {module_version}")
    print("  Required dependency edges:")
    if dependency_edges:
        for path, dependency, actual in dependency_edges:
            print(f"    {path}: {dependency} pinned as {actual}")
    else:
        print("    (none)")

    if errors:
        print("")
        print("Platform dependency validation failed:")
        for error in errors:
            print(f"::error::{error}")
        return 1

    print("")
    print("Platform dependency presence is coherent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
