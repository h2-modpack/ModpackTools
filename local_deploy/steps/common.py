"""
Shared utilities for deploy steps.
"""

import argparse
import glob
import os
import platform
import re
import subprocess
import tomllib


DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.dirname(DEPLOY_DIR)
ROOT_DIR = os.path.dirname(TOOLS_DIR)
DEFAULT_PROFILE = "h2-dev"
DEFAULT_LINK_MODE = "auto"


def is_wsl(system=None, release=None):
    if system is None:
        system = platform.system()
    if system != "Linux":
        return False

    if release is None:
        release = platform.release()
    if "microsoft" in release.lower():
        return True

    try:
        with open("/proc/sys/kernel/osrelease", "r", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def windows_path_to_wsl_path(path):
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    if not match:
        return path

    drive = match.group(1).lower()
    rest = match.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def query_windows_appdata():
    commands = (
        ("cmd.exe", "/C", "echo %APPDATA%"),
        ("powershell.exe", "-NoProfile", "-Command", "[Environment]::GetFolderPath('ApplicationData')"),
    )

    for command in commands:
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue

        if result.returncode != 0:
            continue

        value = result.stdout.strip()
        if re.match(r"^[A-Za-z]:[\\/]", value):
            return value

    return None


def resolve_windows_appdata(env, allow_wsl_query=False, appdata_resolver=None):
    appdata = env.get("APPDATA")
    if appdata:
        return appdata

    if allow_wsl_query:
        resolver = appdata_resolver or query_windows_appdata
        appdata = resolver()
        if appdata:
            return appdata

    return None


def get_profile_path(profile_name, profile_root=None, env=None, system=None, release=None, appdata_resolver=None):
    if env is None:
        env = os.environ
    if system is None:
        system = platform.system()
    running_wsl = is_wsl(system, release)

    if profile_root:
        root = windows_path_to_wsl_path(os.path.expanduser(profile_root)) if running_wsl else os.path.expanduser(profile_root)
        return os.path.join(root, profile_name, "ReturnOfModding")

    if system == "Windows" or running_wsl:
        appdata = resolve_windows_appdata(env, running_wsl, appdata_resolver)
        if not appdata:
            raise RuntimeError(
                "APPDATA is not set and Windows APPDATA could not be queried; "
                "pass --profile-root PATH to the directory containing r2modman profiles"
            )
        if running_wsl:
            appdata = windows_path_to_wsl_path(appdata)
        return os.path.join(appdata, "r2modmanPlus-local", "HadesII", "profiles", profile_name, "ReturnOfModding")

    return os.path.expanduser(f"~/.config/r2modmanPlus-local/HadesII/profiles/{profile_name}/ReturnOfModding")


def get_toml_info(toml_path):
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    package = data.get("package", {})
    namespace = package.get("namespace")
    name = package.get("name")
    if not isinstance(namespace, str) or not namespace:
        raise RuntimeError(f"{toml_path} is missing package.namespace")
    if not isinstance(name, str) or not name:
        raise RuntimeError(f"{toml_path} is missing package.name")
    return namespace, name


def discover_packages():
    """Returns top-level packages, then Submodules/* packages."""
    top_level = sorted(
        d for d in glob.glob(os.path.join(ROOT_DIR, "*"))
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "thunderstore.toml"))
    )
    submodules = sorted(
        d for d in glob.glob(os.path.join(ROOT_DIR, "Submodules", "*"))
        if os.path.isfile(os.path.join(d, "thunderstore.toml"))
    )
    return top_level + submodules


def base_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files/links (default: skip)")
    parser.add_argument(
        "--fast",
        action="store_true",
        help=(
            "Use a cache-backed incremental copy deploy. Only valid when link mode resolves to copy; "
            "trusts the previous deploy cache and does not verify destination contents."
        ),
    )
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help=f"r2modman profile name (default: {DEFAULT_PROFILE})")
    parser.add_argument(
        "--profile-root",
        help="Directory containing r2modman profile folders; overrides automatic profile discovery",
    )
    parser.add_argument(
        "--link-mode",
        choices=("auto", "symlink", "copy"),
        default=DEFAULT_LINK_MODE,
        help="Profile deployment mode (default: auto; WSL to Windows profiles uses copy)",
    )
    return parser
