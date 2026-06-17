#!/usr/bin/env python3
"""Tests for local_deploy/deploy_all.py."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


TOOLS_DIR = Path(__file__).resolve().parents[1]
DEPLOY_DIR = TOOLS_DIR / "local_deploy"
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))

import deploy_all  # noqa: E402


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


def make_smoke_shell(root: Path) -> None:
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "smoke.lua").write_text("return true\n", encoding="utf-8")


def test_smoke_preflight_skips_when_requested() -> None:
    calls = []

    ran = deploy_all.run_smoke_preflight(
        skip_smoke=True,
        root_dir="/unused",
        run=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert_equal(ran, False, "preflight result")
    assert_equal(calls, [], "runner calls")


def test_smoke_preflight_skips_when_script_is_absent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        calls = []

        ran = deploy_all.run_smoke_preflight(
            root_dir=tmp,
            run=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert_equal(ran, False, "preflight result")
    assert_equal(calls, [], "runner calls")


def test_smoke_preflight_runs_shell_smoke_script() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        make_smoke_shell(root)
        calls = []

        def fake_run(command, cwd):
            calls.append((command, cwd))
            return SimpleNamespace(returncode=0)

        ran = deploy_all.run_smoke_preflight(lua_runner="lua5.2", root_dir=tmp, run=fake_run)

    assert_equal(ran, True, "preflight result")
    assert_equal(
        calls,
        [(
            ["lua5.2", "tests/smoke.lua"],
            tmp,
        )],
        "runner calls",
    )


def test_smoke_preflight_fails_before_deploy_on_smoke_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        make_smoke_shell(Path(tmp))

        assert_raises(
            "smoke preflight failed",
            lambda: deploy_all.run_smoke_preflight(
                root_dir=tmp,
                run=lambda command, cwd: SimpleNamespace(returncode=7),
            ),
        )


def test_fast_and_overwrite_are_rejected() -> None:
    assert_raises(
        "--fast and --overwrite",
        lambda: deploy_all.validate_deploy_options(SimpleNamespace(fast=True, overwrite=True)),
    )


def main() -> int:
    tests = [
        test_smoke_preflight_skips_when_requested,
        test_smoke_preflight_skips_when_script_is_absent,
        test_smoke_preflight_runs_shell_smoke_script,
        test_smoke_preflight_fails_before_deploy_on_smoke_error,
        test_fast_and_overwrite_are_rejected,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} deploy_all tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
