"""Shared module roster discovery for shell composition tooling."""

from __future__ import annotations

import tomllib
import configparser
from dataclasses import dataclass
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = TOOLS_DIR.parent
GITMODULES = ROOT_DIR / ".gitmodules"
PACK_CONFIG_NAME = "pack.toml"


@dataclass(frozen=True)
class PackageInfo:
    namespace: str
    name: str
    version: str

    @property
    def thunderstore_id(self) -> str:
        return f"{self.namespace}-{self.name}"


@dataclass(frozen=True)
class CoordinatorPackage:
    path: Path
    toml_path: Path
    package: PackageInfo


@dataclass(frozen=True)
class ModuleRepo:
    folder_name: str
    path: Path
    package: PackageInfo | None

    @property
    def dependency_id(self) -> str:
        if self.package is None:
            return self.folder_name
        return self.package.thunderstore_id

    @property
    def dependency_version(self) -> str:
        if self.package is None:
            return "1.0.0"
        return self.package.version

    @property
    def package_id(self) -> str:
        """Package name without the team prefix, the form pack.toml and create.py use."""
        if self.package is not None:
            return self.package.name
        return self.folder_name.split("-", 1)[-1]


def registered_paths(gitmodules_path: Path = GITMODULES) -> list[Path]:
    if not gitmodules_path.is_file():
        return []

    config = configparser.ConfigParser()
    config.read(gitmodules_path)
    paths: list[Path] = []
    for section in config.sections():
        if config.has_option(section, "path"):
            paths.append(Path(config.get(section, "path").replace("\\", "/")))
    return sorted(paths, key=lambda path: path.as_posix())


def read_toml(path: Path) -> dict:
    with path.open("rb") as file:
        return tomllib.load(file)


def read_package_info(toml_path: Path, *, default_version: str = "1.0.0") -> PackageInfo:
    data = read_toml(toml_path)
    package = data.get("package", {})
    namespace = package.get("namespace")
    name = package.get("name")
    version = package.get("versionNumber", default_version)
    if not isinstance(namespace, str) or not namespace:
        raise RuntimeError(f"{toml_path} is missing package.namespace")
    if not isinstance(name, str) or not name:
        raise RuntimeError(f"{toml_path} is missing package.name")
    if not isinstance(version, str) or not version:
        version = default_version
    return PackageInfo(namespace=namespace, name=name, version=version)


def registered_module_paths(root_dir: Path = ROOT_DIR, gitmodules_path: Path | None = None) -> list[Path]:
    source = gitmodules_path or (root_dir / ".gitmodules")
    return [root_dir / path for path in registered_paths(source) if path.parts[:1] == ("Submodules",)]


def module_repo_from_dir(path: Path) -> ModuleRepo:
    toml_path = path / "thunderstore.toml"
    package = read_package_info(toml_path) if toml_path.is_file() else None
    return ModuleRepo(folder_name=path.name, path=path, package=package)


def discover_module_repos(root_dir: Path = ROOT_DIR) -> list[ModuleRepo]:
    return [module_repo_from_dir(path) for path in registered_module_paths(root_dir)]


def find_coordinator_package(root_dir: Path = ROOT_DIR, *, team: str | None = None) -> CoordinatorPackage | None:
    matches: list[CoordinatorPackage] = []
    for path in registered_paths(root_dir / ".gitmodules"):
        if path.parts[:1] == ("Submodules",):
            continue
        entry = root_dir / path
        if not entry.is_dir():
            continue
        toml_path = entry / "thunderstore.toml"
        if not toml_path.is_file():
            continue
        package = read_package_info(toml_path)
        if package.name.endswith("_Modpack") and (team is None or package.namespace == team):
            matches.append(CoordinatorPackage(path=entry, toml_path=toml_path, package=package))

    if not matches:
        return None
    if len(matches) > 1:
        paths = ", ".join(str(match.toml_path) for match in matches)
        raise RuntimeError(f"multiple coordinator thunderstore.toml files matched: {paths}")
    return matches[0]


class PackConfigError(Exception):
    """pack.toml is malformed or disagrees with the registered roster."""


def read_dependency_modules(root_dir: Path = ROOT_DIR) -> list[str] | None:
    """Package ids listed under [dependencies].modules in pack.toml.

    Returns None when the shell has no pack.toml, which means every registered module is a
    dependency (the behavior before pack.toml existed).
    """
    path = root_dir / PACK_CONFIG_NAME
    if not path.is_file():
        return None
    data = read_toml(path)
    dependencies = data.get("dependencies", {})
    modules = dependencies.get("modules") if isinstance(dependencies, dict) else None
    if not isinstance(modules, list) or not all(isinstance(item, str) and item for item in modules):
        raise PackConfigError(f"{path.name} must define [dependencies] modules as a list of package ids")
    duplicates = sorted({item for item in modules if modules.count(item) > 1})
    if duplicates:
        raise PackConfigError(f"{path.name} lists modules more than once: {', '.join(duplicates)}")
    return list(modules)


def split_module_repos(
    repos: list[ModuleRepo],
    dependency_modules: list[str] | None,
) -> tuple[list[ModuleRepo], list[ModuleRepo]]:
    """Split registered modules into (dependencies, standalone).

    With dependency_modules None every module is a dependency. A name that is not a registered
    module is an error: it would silently produce a dependency on nothing.
    """
    if dependency_modules is None:
        return list(repos), []
    known = {repo.package_id for repo in repos}
    unknown = sorted(set(dependency_modules) - known)
    if unknown:
        raise PackConfigError(
            f"{PACK_CONFIG_NAME} lists modules that are not registered submodules: {', '.join(unknown)}"
        )
    listed = set(dependency_modules)
    return (
        [repo for repo in repos if repo.package_id in listed],
        [repo for repo in repos if repo.package_id not in listed],
    )


def dependency_module_repos(root_dir: Path = ROOT_DIR) -> list[ModuleRepo]:
    """Registered modules that are Thunderstore dependencies of the coordinator."""
    repos = discover_module_repos(root_dir)
    dependencies, _ = split_module_repos(repos, read_dependency_modules(root_dir))
    return dependencies


def standalone_module_repos(root_dir: Path = ROOT_DIR) -> list[ModuleRepo]:
    """Registered modules that ship with the pack but are not its dependencies."""
    repos = discover_module_repos(root_dir)
    _, standalone = split_module_repos(repos, read_dependency_modules(root_dir))
    return standalone


PACK_CONFIG_HEADER = (
    "# Modules that are Thunderstore dependencies of the coordinator, by package id.\n"
    "# Registered modules not listed here are standalone: they boot in smoke, deploy locally and\n"
    "# release with the pack, but the pack does not install or pin them.\n"
)


def write_dependency_modules(modules: list[str], root_dir: Path = ROOT_DIR) -> Path:
    """Write pack.toml with the given dependency modules (sorted, one per line)."""
    path = root_dir / PACK_CONFIG_NAME
    body = "".join(f'    "{name}",\n' for name in sorted(set(modules)))
    path.write_text(f"{PACK_CONFIG_HEADER}[dependencies]\nmodules = [\n{body}]\n", encoding="utf-8", newline="\n")
    return path


def add_dependency_module(package_id: str, root_dir: Path = ROOT_DIR) -> bool:
    """Add a module to pack.toml, creating it from the current roster when absent. True if changed."""
    listed = read_dependency_modules(root_dir)
    if listed is None:
        # Without a pack.toml every registered module is already a dependency; write that down.
        everyone = [repo.package_id for repo in discover_module_repos(root_dir)]
        write_dependency_modules(everyone + [package_id], root_dir)
        return True
    if package_id in listed:
        return False
    write_dependency_modules(listed + [package_id], root_dir)
    return True


def set_new_module_membership(package_id: str, as_dependency: bool, root_dir: Path = ROOT_DIR) -> str:
    """Record whether a newly registered module is a coordinator dependency; returns a one-line summary.

    Standalone is the default. When the shell has no pack.toml yet, creating one that lists the other
    registered modules is what makes the new one standalone.
    """
    if as_dependency:
        changed = add_dependency_module(package_id, root_dir)
        return f"{package_id} is a coordinator dependency ({'added to' if changed else 'already in'} {PACK_CONFIG_NAME})"
    listed = read_dependency_modules(root_dir)
    if listed is None:
        others = [repo.package_id for repo in discover_module_repos(root_dir) if repo.package_id != package_id]
        write_dependency_modules(others, root_dir)
        return f"{package_id} is standalone (created {PACK_CONFIG_NAME} listing the other modules as dependencies)"
    if package_id in listed:
        return f"{package_id} is listed in {PACK_CONFIG_NAME}, so it is a coordinator dependency"
    return f"{package_id} is standalone (not in {PACK_CONFIG_NAME}); pass --dependency to make it a coordinator dependency"
