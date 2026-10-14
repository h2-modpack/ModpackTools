"""
Register existing repos in Submodules/ as git submodules.

Scans Submodules/ for git repos not yet registered in .gitmodules, reads their
remote URL, and runs `git submodule add --force` to register them.

With --prune, also removes .gitmodules entries whose Submodules/ folder is gone.

After register/prune, syncs the coordinator module's managed Thunderstore
dependency block. Smoke tests read the registered roster from .gitmodules.

Repos with no remote configured are skipped with a warning - create the GitHub
repo first, add it as `origin`, then re-run this script.

Usage (run from anywhere inside the shell repo):
  python ModpackTools/new_module/register_submodules.py
  python ModpackTools/new_module/register_submodules.py --prune
"""

import os
import sys
import subprocess
import configparser


TOOLS_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR       = os.path.dirname(TOOLS_DIR)
SUBMODULES_DIR = os.path.join(ROOT_DIR, "Submodules")
GITMODULES     = os.path.join(ROOT_DIR, ".gitmodules")

from coordinator_deps import update_coordinator_deps
from module_roster import PACK_CONFIG_NAME, discover_module_repos, read_dependency_modules


# =============================================================================
# HELPERS
# =============================================================================

def run(cmd, cwd=None, capture=False):
    return subprocess.run(cmd, cwd=cwd, capture_output=capture, text=True)


def git(args, cwd=None):
    return run(["git"] + args, cwd=cwd, capture=True)


def registered_paths():
    """Return set of forward-slash submodule paths already in .gitmodules."""
    if not os.path.exists(GITMODULES):
        return set()
    cfg = configparser.ConfigParser()
    cfg.read(GITMODULES)
    paths = set()
    for section in cfg.sections():
        if cfg.has_option(section, "path"):
            paths.add(cfg.get(section, "path").replace("\\", "/"))
    return paths


def remote_url(repo_path):
    result = git(["remote", "get-url", "origin"], cwd=repo_path)
    return result.stdout.strip() if result.returncode == 0 else None


def current_branch(repo_path):
    result = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    branch = result.stdout.strip() if result.returncode == 0 else ""
    return branch if branch and branch != "HEAD" else "main"


# =============================================================================
# MAIN
# =============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Register submodules and optionally prune removed ones")
    parser.add_argument("--prune", action="store_true", help="Remove .gitmodules entries whose folder no longer exists")
    args = parser.parse_args()

    if not os.path.isdir(SUBMODULES_DIR):
        print("No Submodules/ directory found.")
        sys.exit(0)

    already_registered = registered_paths()

    # -------------------------------------------------------------------------
    # Prune: entries in .gitmodules with no corresponding folder
    # -------------------------------------------------------------------------
    if args.prune:
        to_prune = [
            rel for rel in already_registered
            if rel.startswith("Submodules/") and not os.path.isdir(os.path.join(ROOT_DIR, rel))
        ]

        if to_prune:
            print(f"Pruning {len(to_prune)} removed submodule(s):\n")
            for rel in sorted(to_prune):
                print(f"  {rel}")

            print()
            failed_prune = []
            for rel in sorted(to_prune):
                print(f">>> deinit + rm {rel} ...", end=" ", flush=True)
                r1 = git(["submodule", "deinit", "-f", rel], cwd=ROOT_DIR)
                r2 = git(["rm", "-f", rel], cwd=ROOT_DIR)
                if r1.returncode == 0 and r2.returncode == 0:
                    print("done.")
                else:
                    print("FAILED.")
                    if r1.returncode != 0:
                        print(f"    deinit: {r1.stderr.strip()}")
                    if r2.returncode != 0:
                        print(f"    rm:     {r2.stderr.strip()}")
                    failed_prune.append(rel)

            print()
            if failed_prune:
                print(f"  {len(failed_prune)} prune(s) failed - check errors above.")
                sys.exit(1)
            else:
                print(f"  Pruned. Re-reading registered paths...")
                already_registered = registered_paths()
        else:
            print("Nothing to prune.")

    # -------------------------------------------------------------------------
    # Register: folders in Submodules/ not yet in .gitmodules
    # -------------------------------------------------------------------------
    to_register = []
    warnings    = []

    for entry in sorted(os.scandir(SUBMODULES_DIR), key=lambda e: e.name):
        name = entry.name
        path = entry.path
        rel  = f"Submodules/{name}"

        if not entry.is_dir() or name.startswith("."):
            continue

        if not os.path.exists(os.path.join(path, ".git")):
            continue  # not a git repo, silently skip (.gitkeep etc.)

        if rel in already_registered:
            print(f"  ok      {rel}")
            continue

        url = remote_url(path)
        if not url:
            warnings.append(name)
            print(f"  WARN    {name}  - no remote origin. Create a GitHub repo,")
            print(f"          run `git remote add origin <url>` inside it, then re-run.")
            continue

        branch = current_branch(path)
        to_register.append((name, rel, url, branch))

    if warnings:
        print()

    if not to_register:
        print("\nNothing new to register.")
    else:
        print(f"\nRegistering {len(to_register)} submodule(s):\n")
        for name, rel, url, branch in to_register:
            print(f"  {rel}")
            print(f"    url:    {url}")
            print(f"    branch: {branch}")

        print()

        failed = []
        for name, rel, url, branch in to_register:
            print(f">>> {rel} ...", end=" ", flush=True)
            result = run(
                ["git", "submodule", "add", "--force", "--branch", branch, url, rel],
                cwd=ROOT_DIR,
            )
            if result.returncode == 0:
                print("done.")
            else:
                print("FAILED.")
                print(f"    {result.stderr.strip()}")
                failed.append(rel)

        print()
        if failed:
            print(f"  {len(failed)} failed - check errors above.")
            sys.exit(1)

    # -------------------------------------------------------------------------
    # Sync coordinator module dependencies
    # -------------------------------------------------------------------------
    print()
    update_coordinator_deps()

    listed = read_dependency_modules()
    if listed is None:
        print(f"\n  NOTE: no {PACK_CONFIG_NAME}, so every registered module is a coordinator dependency.")
    elif to_register:
        listed_set = set(listed)
        for repo in discover_module_repos():
            if repo.package_id not in listed_set:
                print(f"  NOTE: {repo.package_id} is standalone; add it to {PACK_CONFIG_NAME} to make the coordinator depend on it.")


if __name__ == "__main__":
    main()
