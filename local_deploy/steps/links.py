"""
Deploy package folders into an r2modman profile.
"""

import json
import os
import platform
import shutil

from .common import discover_packages, get_profile_path, get_toml_info, is_wsl

CACHE_DIR_NAME = ".adamant-deploy-cache"
CACHE_SCHEMA_VERSION = 1


def is_windows_mount_path(path):
    abs_path = os.path.abspath(path)
    parts = abs_path.split(os.sep)
    return len(parts) > 3 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha()


def is_wsl_unc_path(path):
    normalized = str(path).replace("/", "\\").lower()
    if normalized.startswith("\\\\wsl$\\") or normalized.startswith("\\\\wsl.localhost\\"):
        return True
    abs_normalized = os.path.abspath(path).replace("/", "\\").lower()
    return abs_normalized.startswith("\\\\wsl$\\") or abs_normalized.startswith("\\\\wsl.localhost\\")


def remove_existing(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
        return
    if os.path.isdir(path):
        shutil.rmtree(path)


def normalize_relpath(path):
    return path.replace(os.sep, "/")


def local_path(root, relpath):
    return os.path.join(root, *relpath.split("/"))


def build_source_manifest(source_root):
    files = {}
    dirs = []
    for current_root, dirnames, filenames in os.walk(source_root):
        dirnames.sort()
        filenames.sort()
        rel_root = os.path.relpath(current_root, source_root)
        if rel_root != ".":
            dirs.append(normalize_relpath(rel_root))

        for filename in filenames:
            source_file = os.path.join(current_root, filename)
            rel_file = filename if rel_root == "." else os.path.join(rel_root, filename)
            stat = os.stat(source_file)
            files[normalize_relpath(rel_file)] = {
                "size": stat.st_size,
                "mtimeNs": stat.st_mtime_ns,
            }

    return {
        "schemaVersion": CACHE_SCHEMA_VERSION,
        "sourceRoot": source_root,
        "files": files,
        "dirs": dirs,
    }


def read_deploy_cache(cache_path, source_root, dest_root):
    try:
        with open(cache_path, "r", encoding="utf-8") as handle:
            cache = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    if cache.get("schemaVersion") != CACHE_SCHEMA_VERSION:
        return None
    if cache.get("sourceRoot") != source_root or cache.get("destRoot") != dest_root:
        return None
    if not isinstance(cache.get("files"), dict) or not isinstance(cache.get("dirs"), list):
        return None
    return cache


def write_deploy_cache(cache_path, manifest, dest_root):
    cache = {
        "schemaVersion": CACHE_SCHEMA_VERSION,
        "sourceRoot": manifest["sourceRoot"],
        "destRoot": dest_root,
        "files": manifest["files"],
        "dirs": manifest["dirs"],
    }
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, sort_keys=True, indent=2)
        handle.write("\n")


def seed_fast_copy(abs_target, abs_link, cache_path, manifest):
    if os.path.exists(abs_link) or os.path.lexists(abs_link):
        remove_existing(abs_link)

    os.makedirs(os.path.dirname(abs_link), exist_ok=True)
    shutil.copytree(abs_target, abs_link)
    write_deploy_cache(cache_path, manifest, abs_link)
    print(f"  FAST-COPIED: {abs_link} (cache seeded)")
    return True


def copy_fast_changed_files(abs_target, abs_link, old_cache, manifest):
    stats = {
        "copied": 0,
        "removed": 0,
        "unchanged": 0,
    }
    old_files = old_cache["files"]
    old_dirs = set(old_cache["dirs"])
    current_files = manifest["files"]
    current_dirs = set(manifest["dirs"])

    for relpath in sorted(set(old_files) - set(current_files)):
        remove_existing(local_path(abs_link, relpath))
        stats["removed"] += 1

    for relpath in sorted(old_dirs - current_dirs, key=lambda value: value.count("/"), reverse=True):
        remove_existing(local_path(abs_link, relpath))
        stats["removed"] += 1

    for relpath, metadata in current_files.items():
        if old_files.get(relpath) == metadata:
            stats["unchanged"] += 1
            continue

        source_file = local_path(abs_target, relpath)
        dest_file = local_path(abs_link, relpath)
        if os.path.exists(dest_file) or os.path.lexists(dest_file):
            remove_existing(dest_file)
        os.makedirs(os.path.dirname(dest_file), exist_ok=True)
        shutil.copy2(source_file, dest_file)
        stats["copied"] += 1

    return stats


def resolve_link_mode(link_mode, profile_path, source_root=None):
    if link_mode != "auto":
        return link_mode
    if is_wsl() and is_windows_mount_path(profile_path):
        return "copy"
    if platform.system() == "Windows" and source_root and is_wsl_unc_path(source_root):
        return "copy"
    return "symlink"


def create_symlink(target, link_path, overwrite):
    abs_target = os.path.abspath(target)
    abs_link = os.path.abspath(link_path)

    if not os.path.isdir(abs_target):
        return False

    if os.path.exists(abs_link) or os.path.lexists(abs_link):
        if not overwrite:
            print(f"  SKIP (exists): {abs_link}")
            return False
        remove_existing(abs_link)

    os.makedirs(os.path.dirname(abs_link), exist_ok=True)
    os.symlink(abs_target, abs_link, target_is_directory=True)
    print(f"  LINKED: {abs_link}")
    return True


def copy_tree(target, link_path, overwrite):
    abs_target = os.path.abspath(target)
    abs_link = os.path.abspath(link_path)

    if not os.path.isdir(abs_target):
        return False

    if os.path.exists(abs_link) or os.path.lexists(abs_link):
        if not overwrite:
            print(f"  SKIP (exists): {abs_link}")
            return False
        remove_existing(abs_link)

    os.makedirs(os.path.dirname(abs_link), exist_ok=True)
    shutil.copytree(abs_target, abs_link)
    print(f"  COPIED: {abs_link}")
    return True


def fast_copy_tree(target, link_path, cache_path):
    abs_target = os.path.abspath(target)
    abs_link = os.path.abspath(link_path)

    if not os.path.isdir(abs_target):
        return False
    if not cache_path:
        raise RuntimeError("--fast copy deploy requires a cache path")

    manifest = build_source_manifest(abs_target)
    cache = read_deploy_cache(cache_path, abs_target, abs_link)
    if cache is None or not os.path.isdir(abs_link) or os.path.islink(abs_link):
        return seed_fast_copy(abs_target, abs_link, cache_path, manifest)

    stats = copy_fast_changed_files(abs_target, abs_link, cache, manifest)
    write_deploy_cache(cache_path, manifest, abs_link)
    print(
        f"  FAST-SYNCED: {abs_link} "
        f"({stats['copied']} copied, {stats['removed']} removed, {stats['unchanged']} unchanged)"
    )
    return True


def deploy_path(target, link_path, overwrite, link_mode, fast=False, cache_path=None):
    if fast:
        if overwrite:
            raise RuntimeError("--fast and --overwrite cannot be combined")
        if link_mode != "copy":
            raise RuntimeError("--fast is only supported when link mode resolves to copy")
        return fast_copy_tree(target, link_path, cache_path)
    if link_mode == "copy":
        return copy_tree(target, link_path, overwrite)
    return create_symlink(target, link_path, overwrite)


def deploy(overwrite, profile, profile_root=None, link_mode="auto", fast=False):
    profile_path = get_profile_path(profile, profile_root)
    resolved_mode = resolve_link_mode(link_mode, profile_path, os.getcwd())
    if fast and overwrite:
        raise RuntimeError("--fast and --overwrite cannot be combined")
    if fast and resolved_mode != "copy":
        raise RuntimeError("--fast is only supported when link mode resolves to copy")

    print(f"\n  Profile deployment to: {profile}")
    print(f"  Profile path: {profile_path}")
    print(f"  Link mode: {resolved_mode}")
    print(f"  Overwrite: {overwrite}")
    print(f"  Fast: {fast}\n")

    count = 0
    for package_dir in discover_packages():
        namespace, name = get_toml_info(os.path.join(package_dir, "thunderstore.toml"))
        package_name = f"{namespace}-{name}"

        print(f"--- {package_name} ---")
        cache_root = os.path.join(profile_path, CACHE_DIR_NAME, package_name)
        deploy_path(
            os.path.join(package_dir, "src"),
            os.path.join(profile_path, "plugins", package_name),
            overwrite,
            resolved_mode,
            fast,
            os.path.join(cache_root, "plugins.json"),
        )
        deploy_path(
            os.path.join(package_dir, "data"),
            os.path.join(profile_path, "plugins_data", package_name),
            overwrite,
            resolved_mode,
            fast,
            os.path.join(cache_root, "plugins_data.json"),
        )
        count += 1

    print(f"\nDone. {count} packages processed.\n")
    return count
