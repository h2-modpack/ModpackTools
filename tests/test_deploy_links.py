#!/usr/bin/env python3
"""Tests for local_deploy/steps/links.py."""

from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
DEPLOY_DIR = TOOLS_DIR / "local_deploy"
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))

from steps import links  # noqa: E402


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_raises(message: str, func) -> Exception:
    try:
        func()
    except Exception as exc:
        if message not in str(exc):
            raise AssertionError(f"expected {message!r} in {exc!r}") from exc
        return exc
    raise AssertionError(f"expected exception containing {message!r}")


def track_copy2():
    calls = []
    old_copy2 = links.shutil.copy2

    def fake_copy2(source, dest):
        calls.append((Path(source).name, Path(dest).name))
        return old_copy2(source, dest)

    links.shutil.copy2 = fake_copy2
    return calls, old_copy2


def cache_path(root: Path) -> Path:
    return root / "profile" / ".adamant-deploy-cache" / "Package" / "plugins.json"


def test_auto_mode_uses_copy_for_wsl_windows_profile() -> None:
    old_is_wsl = links.is_wsl
    try:
        links.is_wsl = lambda: True
        mode = links.resolve_link_mode("auto", "/mnt/c/Users/Example/AppData/Roaming/r2modmanPlus-local")
    finally:
        links.is_wsl = old_is_wsl

    assert_equal(mode, "copy", "resolved mode")


def test_auto_mode_uses_symlink_for_same_side_profile() -> None:
    old_is_wsl = links.is_wsl
    try:
        links.is_wsl = lambda: True
        mode = links.resolve_link_mode("auto", "/home/example/.config/r2modmanPlus-local")
    finally:
        links.is_wsl = old_is_wsl

    assert_equal(mode, "symlink", "resolved mode")


def test_auto_mode_uses_copy_for_windows_python_wsl_unc_source() -> None:
    old_is_wsl = links.is_wsl
    old_platform_system = links.platform.system
    try:
        links.is_wsl = lambda: False
        links.platform.system = lambda: "Windows"
        mode = links.resolve_link_mode(
            "auto",
            r"C:\Users\Example\AppData\Roaming\r2modmanPlus-local\HadesII\profiles\h2-dev\ReturnOfModding",
            r"\\wsl.localhost\Ubuntu\home\example\run-director-modpack",
        )
    finally:
        links.is_wsl = old_is_wsl
        links.platform.system = old_platform_system

    assert_equal(mode, "copy", "resolved mode")


def test_copy_tree_overwrites_existing_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "profile" / "plugins" / "Package"

        source.mkdir()
        (source / "manifest.json").write_text('{"new": true}\n', encoding="utf-8")
        target.mkdir(parents=True)
        (target / "stale.txt").write_text("old\n", encoding="utf-8")

        copied = links.copy_tree(str(source), str(target), overwrite=True)

        if not copied:
            raise AssertionError("expected copy_tree to copy")
        assert_equal((target / "manifest.json").read_text(encoding="utf-8"), '{"new": true}\n', "copied manifest")
        if (target / "stale.txt").exists():
            raise AssertionError("expected stale file to be removed")


def test_copy_tree_overwrite_syncs_in_place_and_keeps_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "profile" / "plugins" / "Package"

        (source / "nested").mkdir(parents=True)
        (source / "main.lua").write_text("new\n", encoding="utf-8")
        (source / "nested" / "ui.lua").write_text("ui\n", encoding="utf-8")
        (target / "stale_dir").mkdir(parents=True)
        (target / "stale.txt").write_text("old\n", encoding="utf-8")
        (target / "stale_dir" / "gone.lua").write_text("old\n", encoding="utf-8")
        (target / "main.lua").write_text("old\n", encoding="utf-8")
        inode_before = os.stat(target).st_ino

        copied = links.copy_tree(str(source), str(target), overwrite=True)

        if not copied:
            raise AssertionError("expected copy_tree to sync")
        if os.stat(target).st_ino != inode_before:
            raise AssertionError("expected copy_tree to keep the destination directory (watched by the game)")
        if (target / "main.lua").read_text(encoding="utf-8") != "new\n":
            raise AssertionError("expected changed file to be replaced")
        if (target / "nested" / "ui.lua").read_text(encoding="utf-8") != "ui\n":
            raise AssertionError("expected nested file to be copied")
        if (target / "stale.txt").exists() or (target / "stale_dir").exists():
            raise AssertionError("expected stale entries to be removed")


def test_fast_copy_tree_seed_syncs_existing_directory_in_place() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "profile" / "plugins" / "Package"
        cache = root / "profile" / ".adamant-deploy-cache" / "Package" / "plugins.json"

        source.mkdir()
        (source / "main.lua").write_text("new\n", encoding="utf-8")
        target.mkdir(parents=True)
        (target / "stale.txt").write_text("old\n", encoding="utf-8")
        inode_before = os.stat(target).st_ino

        links.fast_copy_tree(str(source), str(target), str(cache))

        if os.stat(target).st_ino != inode_before:
            raise AssertionError("expected seed to keep the destination directory")
        if (target / "stale.txt").exists():
            raise AssertionError("expected stale file to be removed")
        if (target / "main.lua").read_text(encoding="utf-8") != "new\n":
            raise AssertionError("expected source file to be copied")
        if not cache.exists():
            raise AssertionError("expected cache to be seeded")


def test_fast_copy_tree_seeds_cache_with_full_copy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "profile" / "plugins" / "Package"
        cache = cache_path(root)

        source.mkdir()
        (source / "manifest.json").write_text('{"new": true}\n', encoding="utf-8")

        copied = links.fast_copy_tree(str(source), str(target), str(cache))

        if not copied:
            raise AssertionError("expected fast copy to copy")
        assert_equal((target / "manifest.json").read_text(encoding="utf-8"), '{"new": true}\n', "copied manifest")
        if not cache.is_file():
            raise AssertionError("expected deploy cache to be written")


def test_fast_copy_tree_trusts_cache_for_unchanged_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "profile" / "plugins" / "Package"
        cache = cache_path(root)

        source.mkdir()
        (source / "same.lua").write_text("same\n", encoding="utf-8")
        links.fast_copy_tree(str(source), str(target), str(cache))
        (target / "same.lua").write_text("manual edit\n", encoding="utf-8")

        calls, old_copy2 = track_copy2()
        try:
            links.fast_copy_tree(str(source), str(target), str(cache))
        finally:
            links.shutil.copy2 = old_copy2

        assert_equal(calls, [], "copy2 calls")
        assert_equal((target / "same.lua").read_text(encoding="utf-8"), "manual edit\n", "trusted destination")


def test_fast_copy_tree_copies_changed_source_files_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "profile" / "plugins" / "Package"
        cache = cache_path(root)

        source.mkdir()
        (source / "same.lua").write_text("same\n", encoding="utf-8")
        (source / "changed.lua").write_text("old\n", encoding="utf-8")
        links.fast_copy_tree(str(source), str(target), str(cache))
        (source / "changed.lua").write_text("new content\n", encoding="utf-8")

        calls, old_copy2 = track_copy2()
        try:
            links.fast_copy_tree(str(source), str(target), str(cache))
        finally:
            links.shutil.copy2 = old_copy2

        assert_equal(calls, [("changed.lua", "changed.lua")], "copy2 calls")
        assert_equal((target / "changed.lua").read_text(encoding="utf-8"), "new content\n", "changed target")


def test_fast_copy_tree_removes_cached_stale_directories() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "profile" / "plugins" / "Package"
        cache = cache_path(root)

        source.mkdir()
        (source / "kept.lua").write_text("kept\n", encoding="utf-8")
        stale_dir = source / "stale"
        stale_dir.mkdir()
        (stale_dir / "old.lua").write_text("old\n", encoding="utf-8")
        links.fast_copy_tree(str(source), str(target), str(cache))
        (stale_dir / "old.lua").unlink()
        stale_dir.rmdir()

        links.fast_copy_tree(str(source), str(target), str(cache))

        if (target / "stale").exists():
            raise AssertionError("expected stale directory to be removed")
        assert_equal((target / "kept.lua").read_text(encoding="utf-8"), "kept\n", "kept target")


def test_fast_copy_tree_replaces_destination_directory_with_source_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "profile" / "plugins" / "Package"
        cache = cache_path(root)

        entry_dir = source / "entry.lua"
        entry_dir.mkdir(parents=True)
        (entry_dir / "old.lua").write_text("old\n", encoding="utf-8")
        links.fast_copy_tree(str(source), str(target), str(cache))
        (entry_dir / "old.lua").unlink()
        entry_dir.rmdir()
        (source / "entry.lua").write_text("file\n", encoding="utf-8")

        links.fast_copy_tree(str(source), str(target), str(cache))

        if not (target / "entry.lua").is_file():
            raise AssertionError("expected destination directory to be replaced by source file")
        assert_equal((target / "entry.lua").read_text(encoding="utf-8"), "file\n", "replaced file")


def test_fast_copy_tree_replaces_destination_file_with_source_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "profile" / "plugins" / "Package"
        cache = cache_path(root)

        source.mkdir()
        (source / "nested").write_text("old file\n", encoding="utf-8")
        links.fast_copy_tree(str(source), str(target), str(cache))
        (source / "nested").unlink()

        nested = source / "nested"
        nested.mkdir()
        (nested / "entry.lua").write_text("nested\n", encoding="utf-8")

        links.fast_copy_tree(str(source), str(target), str(cache))

        if not (target / "nested").is_dir():
            raise AssertionError("expected destination file to be replaced by source directory")
        assert_equal((target / "nested" / "entry.lua").read_text(encoding="utf-8"), "nested\n", "nested file")


def test_fast_deploy_path_rejects_overwrite_and_symlink_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        target = root / "profile" / "plugins" / "Package"
        source.mkdir()

        assert_raises(
            "--fast and --overwrite",
            lambda: links.deploy_path(str(source), str(target), True, "copy", True, str(cache_path(root))),
        )
        assert_raises(
            "link mode resolves to copy",
            lambda: links.deploy_path(str(source), str(target), False, "symlink", True, str(cache_path(root))),
        )


def main() -> int:
    tests = [
        test_auto_mode_uses_copy_for_wsl_windows_profile,
        test_auto_mode_uses_symlink_for_same_side_profile,
        test_auto_mode_uses_copy_for_windows_python_wsl_unc_source,
        test_copy_tree_overwrites_existing_directory,
        test_copy_tree_overwrite_syncs_in_place_and_keeps_directory,
        test_fast_copy_tree_seed_syncs_existing_directory_in_place,
        test_fast_copy_tree_seeds_cache_with_full_copy,
        test_fast_copy_tree_trusts_cache_for_unchanged_files,
        test_fast_copy_tree_copies_changed_source_files_only,
        test_fast_copy_tree_removes_cached_stale_directories,
        test_fast_copy_tree_replaces_destination_directory_with_source_file,
        test_fast_copy_tree_replaces_destination_file_with_source_directory,
        test_fast_deploy_path_rejects_overwrite_and_symlink_mode,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} deploy links tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
