"""
Sync the coordinator module's managed Thunderstore dependency block.
"""

import os
import re
from pathlib import Path

from module_roster import PackConfigError, dependency_module_repos, find_coordinator_package, standalone_module_repos


TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(TOOLS_DIR)

MARKER_START = "# -- submodules-start --"
MARKER_END = "# -- submodules-end --"


def find_coordinator_toml():
    """Find the coordinator module's thunderstore.toml in root-level folders."""
    coordinator = find_coordinator_package(Path(ROOT_DIR))
    return str(coordinator.toml_path) if coordinator is not None else None


def update_coordinator_deps():
    """Sync the managed submodule block in the coordinator thunderstore.toml."""
    print("Syncing coordinator module dependencies...")
    print("  Detecting coordinator module: scanning root-level folders for a thunderstore.toml")
    print("  whose package name ends in '_Modpack'...")

    coordinator_toml = find_coordinator_toml()
    if not coordinator_toml:
        print("  WARN  No coordinator module found, skipping dep sync.")
        return

    print(f"  found   {os.path.relpath(coordinator_toml, ROOT_DIR)}")
    print(f"  Replacing managed block between '{MARKER_START}' and '{MARKER_END}'")
    print("  (infrastructure deps above the markers are left untouched)")

    try:
        repos = dependency_module_repos(Path(ROOT_DIR))
        standalone = standalone_module_repos(Path(ROOT_DIR))
    except PackConfigError as exc:
        print(f"  ERROR {exc}")
        raise SystemExit(1) from exc
    for repo in standalone:
        print(f"  standalone  {repo.dependency_id} (not in pack.toml dependencies; left out of the block)")
    lines = [f'{repo.dependency_id} = "{repo.dependency_version}"' for repo in repos]
    block = MARKER_START + "\n" + "\n".join(lines) + "\n" + MARKER_END

    text = open(coordinator_toml, encoding="utf-8").read()

    if MARKER_START in text:
        pattern = re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END)
        new_text = re.sub(pattern, block, text, flags=re.DOTALL)
    else:
        dep_header = "[package.dependencies]"
        if dep_header not in text:
            print("  WARN  No [package.dependencies] section in coordinator toml, skipping dep sync.")
            return
        match = re.search(r"(\[package\.dependencies\].*?)(\n\[)", text, re.DOTALL)
        if match:
            new_text = text[:match.start(2)] + "\n" + block + "\n" + text[match.start(2):]
        else:
            new_text = text.rstrip() + "\n" + block + "\n"

    open(coordinator_toml, "w", encoding="utf-8").write(new_text)
    print(f"  synced  coordinator deps ({len(repos)} submodules)  ->  {os.path.relpath(coordinator_toml, ROOT_DIR)}")
    print()
    print("  NOTE: Run `ModpackTools/run ModpackTools/local_deploy/deploy_all.py --overwrite` to deploy changes to the game.")
