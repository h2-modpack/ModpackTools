#!/usr/bin/env python3
"""Tests for github/prepare_package_release.py."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TEST_DIR.parent

sys.path.insert(0, str(TOOLS_DIR / "github"))
import prepare_package_release as prep  # noqa: E402


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_raises(title: str, func) -> prep.ReleasePrepError:
    try:
        func()
    except prep.ReleasePrepError as exc:
        assert_equal(exc.title, title, "error title")
        return exc
    raise AssertionError(f"expected ReleasePrepError titled {title!r}")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def write(repo: Path, path: str, content: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


def commit(repo: Path, message: str, body: str | None = None) -> None:
    git(repo, "add", ".")
    args = ["commit", "-m", message]
    if body is not None:
        args.extend(["-m", body])
    git(repo, *args)


def init_repo() -> Path:
    root = Path(tempfile.mkdtemp())
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    return root


def test_parse_commits_groups_visible_conventional_types() -> None:
    commits = [
        prep.Commit("111111111111", "feat(ui): add profile button", ""),
        prep.Commit("222222222222", "fix: restore fallback spacing", ""),
        prep.Commit("333333333333", "chore: update tooling", ""),
        prep.Commit("444444444444", "refactor!: remove legacy API", "BREAKING CHANGE: old API removed\n\nMore."),
    ]
    entries = prep.parse_commits(commits)

    assert_equal([entry.section for entry in entries], [
        "Added",
        "Fixed",
        "Breaking Changes",
        "Changed",
    ], "entry sections")
    assert_equal(entries[0].text, "ui: add profile button (1111111)", "scoped entry")
    assert_equal(entries[2].text, "old API removed (4444444)", "breaking entry")


def test_render_section_rejects_empty_without_allow_empty() -> None:
    assert_raises(
        "Empty changelog",
        lambda: prep.render_section("1.2.0", date(2026, 6, 9), [], False),
    )


def test_render_section_allows_empty_release() -> None:
    rendered = prep.render_section("1.2.0", date(2026, 6, 9), [], True)
    if "- No user-facing changes." not in rendered:
        raise AssertionError(f"missing empty-release note:\n{rendered}")


def test_update_changelog_inserts_after_unreleased() -> None:
    updated = prep.update_changelog(
        "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n",
        "1.1.0",
        "## [1.1.0] - 2026-06-09\n\n### Added\n\n- Thing\n",
    )
    expected = "# Changelog\n\n## [Unreleased]\n\n## [1.1.0] - 2026-06-09\n\n### Added\n\n- Thing\n\n## [1.0.0] - 2026-01-01\n"
    assert_equal(updated, expected, "updated changelog")


def test_find_changelog_section_extracts_existing_release() -> None:
    section = prep.find_changelog_section(
        "# Changelog\n\n## [Unreleased]\n\n## [1.1.0] - 2026-06-09\n\n### Fixed\n\n- Thing\n\n## [1.0.0] - 2026-01-01\n",
        "1.1.0",
    )
    assert_equal(section, "## [1.1.0] - 2026-06-09\n\n### Fixed\n\n- Thing\n", "existing section")


def test_update_thunderstore_config_replaces_version() -> None:
    updated = prep.update_thunderstore_config(
        '[package]\nname = "Package"\nversionNumber = "1.0.0"\n',
        "1.2.0",
    )
    assert_equal(updated, '[package]\nname = "Package"\nversionNumber = "1.2.0"\n', "updated toml")


def test_update_dependency_pins_replaces_matching_table_dependency() -> None:
    updated = prep.update_dependency_pins(
        '\n'.join([
            '[package]',
            '[package.dependencies]',
            'adamant-ModpackLib = "3.0.0"',
            'Other-Team-Package = "1.2.3"',
            '',
        ]),
        [prep.DependencyPin("adamant-ModpackLib", "3.1.0")],
    )
    expected = '\n'.join([
        '[package]',
        '[package.dependencies]',
        'adamant-ModpackLib = "3.1.0"',
        'Other-Team-Package = "1.2.3"',
        '',
    ])
    assert_equal(updated, expected, "dependency pin")


def test_update_dependency_pins_replaces_matching_string_dependency() -> None:
    updated = prep.update_dependency_pins(
        'dependencies = ["h2-modpack-adamant-ModpackLib-3.0.0", "Other-Team-Package-1.2.3"]\n',
        [prep.DependencyPin("h2-modpack-adamant-ModpackLib", "3.1.0")],
    )
    assert_equal(
        updated,
        'dependencies = ["h2-modpack-adamant-ModpackLib-3.1.0", "Other-Team-Package-1.2.3"]\n',
        "string dependency pin",
    )


def test_update_dependency_pins_rejects_missing_dependency() -> None:
    assert_raises(
        "Missing dependency pin",
        lambda: prep.update_dependency_pins(
            'dependencies = ["Other-Team-Package-1.2.3"]\n',
            [prep.DependencyPin("h2-modpack-adamant-ModpackLib", "3.1.0")],
        ),
    )


def test_update_dependency_pins_rejects_duplicate_dependency() -> None:
    assert_raises(
        "Duplicate dependency pin",
        lambda: prep.update_dependency_pins(
            '\n'.join([
                '[package.dependencies]',
                'adamant-ModpackLib = "3.0.0"',
                'adamant-ModpackLib = "3.0.1"',
                '',
            ]),
            [prep.DependencyPin("adamant-ModpackLib", "3.1.0")],
        ),
    )


def test_update_dependency_pins_leaves_unmanaged_dependencies() -> None:
    updated = prep.update_dependency_pins(
        '[package.dependencies]\nManaged-Team-Package = "1.0.0"\nUnmanaged-Team-Package = "9.9.9"\n',
        [prep.DependencyPin("Managed-Team-Package", "1.1.0")],
    )
    assert_equal(
        updated,
        '[package.dependencies]\nManaged-Team-Package = "1.1.0"\nUnmanaged-Team-Package = "9.9.9"\n',
        "unmanaged dependency",
    )


def test_prepare_release_uses_commits_since_previous_tag() -> None:
    repo = init_repo()
    write(repo, "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n")
    write(repo, "thunderstore.toml", '[package]\nversionNumber = "1.0.0"\n')
    write(repo, "src/main.lua", "return {}\n")
    commit(repo, "feat: initial package")
    git(repo, "tag", "1.0.0")

    write(repo, "src/main.lua", "return { updated = true }\n")
    commit(repo, "feat(runtime): add setting")
    write(repo, "README.md", "docs\n")
    commit(repo, "docs: update readme")

    prep.prepare_release(
        repo=repo,
        tag="1.1.0",
        changelog_path=repo / "CHANGELOG.md",
        thunderstore_path=repo / "thunderstore.toml",
        release_notes_path=repo / ".release-notes.md",
        dependency_pins=[],
        allow_empty=False,
        release_date=date(2026, 6, 9),
    )

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    release_notes = (repo / ".release-notes.md").read_text(encoding="utf-8")
    thunderstore = (repo / "thunderstore.toml").read_text(encoding="utf-8")
    if "## [1.1.0] - 2026-06-09" not in changelog:
        raise AssertionError(changelog)
    if "runtime: add setting" not in changelog:
        raise AssertionError(changelog)
    if "update readme" not in changelog:
        raise AssertionError(changelog)
    if release_notes not in changelog:
        raise AssertionError(release_notes)
    if "## [1.1.0] - 2026-06-09" not in release_notes:
        raise AssertionError(release_notes)
    if "runtime: add setting" not in release_notes:
        raise AssertionError(release_notes)
    if 'versionNumber = "1.1.0"' not in thunderstore:
        raise AssertionError(thunderstore)


def test_prepare_release_reuses_existing_changelog_section() -> None:
    repo = init_repo()
    write(
        repo,
        "CHANGELOG.md",
        "# Changelog\n\n## [Unreleased]\n\n## [1.1.0] - 2026-06-09\n\n### Fixed\n\n- Existing note\n",
    )
    write(repo, "thunderstore.toml", '[package]\nversionNumber = "1.1.0"\n')
    write(repo, "src/main.lua", "return {}\n")
    commit(repo, "chore(release): 1.1.0")

    prep.prepare_release(
        repo=repo,
        tag="1.1.0",
        changelog_path=repo / "CHANGELOG.md",
        thunderstore_path=repo / "thunderstore.toml",
        release_notes_path=repo / ".release-notes.md",
        dependency_pins=[],
        allow_empty=False,
        release_date=date(2026, 6, 10),
    )

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    release_notes = (repo / ".release-notes.md").read_text(encoding="utf-8")
    assert_equal(changelog.count("## [1.1.0]"), 1, "single release section")
    assert_equal(
        release_notes,
        "## [1.1.0] - 2026-06-09\n\n### Fixed\n\n- Existing note\n",
        "reused notes",
    )


def test_classify_bump_maps_conventional_types() -> None:
    def bump(*subjects, body=""):
        return prep.classify_bump([prep.Commit(f"{i:040d}", subject, body) for i, subject in enumerate(subjects)])

    assert_equal(bump("fix: a"), "patch", "fix")
    assert_equal(bump("perf(x): a", "fix: b"), "patch", "perf and fix")
    assert_equal(bump("fix: a", "feat: b"), "minor", "feat outranks fix")
    assert_equal(bump("feat!: a", "feat: b"), "major", "bang is breaking")
    assert_equal(bump("fix: a", body="BREAKING CHANGE: gone"), "major", "breaking footer")
    assert_equal(bump("chore: a", "docs: b", "refactor: c", "test: d"), None, "non-releasing types")
    assert_equal(bump("chore(release): 5.0.0"), None, "release commit")
    assert_equal(bump("not conventional"), None, "unparsed subject")
    assert_equal(bump(), None, "no commits")


def test_max_bump_and_bump_version() -> None:
    assert_equal(prep.max_bump(None, "patch", "minor"), "minor", "max bump")
    assert_equal(prep.max_bump(None, None), None, "no bumps")
    assert_equal(prep.bump_version("5.2.3", "patch"), "5.2.4", "patch")
    assert_equal(prep.bump_version("5.2.3", "minor"), "5.3.0", "minor resets patch")
    assert_equal(prep.bump_version("5.2.3", "major"), "6.0.0", "major resets minor and patch")
    assert_equal(prep.bump_version(None, "minor"), "1.0.0", "first release")
    assert_raises("Invalid package version number", lambda: prep.bump_version("v5", "patch"))


def test_commits_since_latest_tag_reads_history_after_newest_version_tag() -> None:
    repo = init_repo()
    write(repo, "a.txt", "1")
    commit(repo, "feat: first")
    git(repo, "tag", "1.0.0")
    write(repo, "a.txt", "2")
    commit(repo, "fix: second")
    git(repo, "tag", "1.0.1")
    write(repo, "a.txt", "3")
    commit(repo, "feat: third")
    write(repo, "a.txt", "4")
    commit(repo, "chore: fourth")

    tag, commits = prep.commits_since_latest_tag(repo)
    assert_equal(tag, "1.0.1", "latest tag")
    assert_equal([item.subject for item in commits], ["chore: fourth", "feat: third"], "commits since tag")
    assert_equal(prep.classify_bump(commits), "minor", "implied bump")

    fresh = init_repo()
    write(fresh, "a.txt", "1")
    commit(fresh, "fix: only")
    tag, commits = prep.commits_since_latest_tag(fresh)
    assert_equal(tag, None, "no tag yet")
    assert_equal(len(commits), 1, "all commits when untagged")


def test_all_version_tags_and_shallow_detection() -> None:
    repo = init_repo()
    write(repo, "a.txt", "1")
    commit(repo, "feat: first")
    git(repo, "tag", "1.0.0")
    write(repo, "a.txt", "2")
    commit(repo, "fix: second")
    git(repo, "tag", "1.0.1")
    git(repo, "tag", "not-a-version")
    write(repo, "a.txt", "3")
    commit(repo, "feat: third")

    assert_equal(prep.all_version_tags(repo), ["1.0.1", "1.0.0"], "version tags only, highest first")
    assert_equal(prep.is_shallow(repo), False, "full repo")

    shallow = Path(tempfile.mkdtemp()) / "clone"
    subprocess.run(
        ["git", "clone", "--depth", "1", "--no-single-branch", repo.as_uri(), str(shallow)],
        check=True,
        capture_output=True,
        text=True,
    )
    git(shallow, "fetch", "--tags", "--quiet")
    assert_equal(prep.is_shallow(shallow), True, "shallow clone")
    # The failure this guards against: tags exist, but none is reachable from the shallow HEAD.
    assert_equal(prep.latest_version_tag(shallow), None, "no reachable tag in a shallow clone")
    assert_equal(prep.all_version_tags(shallow)[0], "1.0.1", "tags still known")


def main() -> int:
    tests = [
        test_parse_commits_groups_visible_conventional_types,
        test_render_section_rejects_empty_without_allow_empty,
        test_render_section_allows_empty_release,
        test_update_changelog_inserts_after_unreleased,
        test_find_changelog_section_extracts_existing_release,
        test_update_thunderstore_config_replaces_version,
        test_update_dependency_pins_replaces_matching_table_dependency,
        test_update_dependency_pins_replaces_matching_string_dependency,
        test_update_dependency_pins_rejects_missing_dependency,
        test_update_dependency_pins_rejects_duplicate_dependency,
        test_update_dependency_pins_leaves_unmanaged_dependencies,
        test_prepare_release_uses_commits_since_previous_tag,
        test_prepare_release_reuses_existing_changelog_section,
        test_classify_bump_maps_conventional_types,
        test_max_bump_and_bump_version,
        test_commits_since_latest_tag_reads_history_after_newest_version_tag,
        test_all_version_tags_and_shallow_detection,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} prepare_package_release tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
