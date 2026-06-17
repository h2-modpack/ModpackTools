"""
Full local deployment: staged package assets, manifests, profile links/copies, and git hooks.

Usage: python deploy_all.py [--overwrite | --fast] [--profile NAME] [--skip-smoke]
"""

import os
import subprocess
import sys
from steps import assets, hooks, links, manifests
from steps.common import ROOT_DIR, base_parser


DEFAULT_SMOKE_SCRIPT = os.path.join("tests", "smoke.lua")


STEPS = [
    ("Staging package assets (icon.png, LICENSE)", lambda args: assets.deploy(args.overwrite)),
    ("Generating manifests", lambda args: manifests.deploy(args.overwrite)),
    ("Deploying profile links", lambda args: links.deploy(args.overwrite, args.profile, args.profile_root, args.link_mode, args.fast)),
    ("Configuring git hooks", lambda args: hooks.deploy(args.overwrite)),
]


def run_smoke_preflight(skip_smoke=False, lua_runner=None, root_dir=ROOT_DIR, run=subprocess.run):
    if skip_smoke:
        print(">>> Smoke preflight skipped by --skip-smoke.")
        return False

    smoke_path = os.path.join(root_dir, DEFAULT_SMOKE_SCRIPT)
    if not os.path.isfile(smoke_path):
        print(f">>> Smoke preflight skipped: {DEFAULT_SMOKE_SCRIPT} not found.")
        return False

    lua = lua_runner or os.environ.get("LUA") or "lua"
    command = [lua, DEFAULT_SMOKE_SCRIPT]
    print(">>> Running smoke preflight...")
    print("  " + " ".join(command))
    result = run(command, cwd=root_dir)
    if result.returncode != 0:
        raise RuntimeError(f"smoke preflight failed with exit code {result.returncode}")
    return True


def validate_deploy_options(args):
    if args.fast and args.overwrite:
        raise RuntimeError("--fast and --overwrite cannot be combined")


def main():
    parser = base_parser("Full local deployment for all mods")
    parser.add_argument("--skip-smoke", action="store_true", help="Skip shell smoke preflight before deployment")
    parser.add_argument(
        "--lua",
        default=os.environ.get("LUA") or "lua",
        help="Lua runner used for smoke preflight (default: LUA env var or lua)",
    )
    args = parser.parse_args()
    validate_deploy_options(args)

    print("\n==========================================================")
    print("  Adamant Modpack - Full Local Deployment")
    print(f"  Profile: {args.profile}")
    print(f"  Overwrite: {args.overwrite}")
    print(f"  Fast: {args.fast}")
    print("==========================================================\n")

    run_smoke_preflight(args.skip_smoke, args.lua)

    for label, deploy_step in STEPS:
        print(f">>> {label}...")
        deploy_step(args)

    print("==========================================================")
    print("  Full deployment complete.")
    print("==========================================================\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
