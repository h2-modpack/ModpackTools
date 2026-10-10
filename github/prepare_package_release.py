#!/usr/bin/env python3
"""Prepare package release files from conventional commits.

Run from a package repo checkout. The script intentionally mutates only the
package-local release files; workflow policy still owns build, publish, commit,
tag, and push behavior.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
CONVENTIONAL_PATTERN = re.compile(r"^([A-Za-z]+)(?:\(([^)]+)\))?(!)?: (.+)$")
BREAKING_PATTERN = re.compile(r"BREAKING[ -]CHANGE:\s*(.+)", re.IGNORECASE | re.DOTALL)
VERSION_LINE_PATTERN = re.compile(r'^(versionNumber\s*=\s*)".*"$', re.MULTILINE)
DEPENDENCY_STRING_PATTERN = re.compile(
    r'(?P<quote>["\'])(?P<package>[A-Za-z0-9_.-]+)-(?P<version>[0-9]+\.[0-9]+\.[0-9]+)(?P=quote)'
)
DEPENDENCY_TABLE_PATTERN = re.compile(
    r'^(?P<prefix>\s*(?P<package>[A-Za-z0-9_.-]+)\s*=\s*)(?P<quote>["\'])(?P<version>[0-9]+\.[0-9]+\.[0-9]+)(?P=quote)',
    re.MULTILINE,
)

SECTION_TITLES = {
    "feat": "Added",
    "fix": "Fixed",
    "perf": "Performance",
    "refactor": "Changed",
    "docs": "Documentation",
}
SECTION_ORDER = [
    "Breaking Changes",
    "Added",
    "Fixed",
    "Performance",
    "Changed",
    "Documentation",
]


class ReleasePrepError(Exception):
    def __init__(self, title: str, message: str):
        super().__init__(message)
        self.title = title
        self.message = message


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str


@dataclass(frozen=True)
class ChangelogEntry:
    section: str
    text: str


@dataclass(frozen=True)
class DependencyPin:
    package: str
    version: str


def run_git(repo: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def validate_tag(tag: str) -> None:
    if not VERSION_PATTERN.match(tag or ""):
        raise ReleasePrepError(
            "Invalid package version number",
            "Version numbers must follow the Major.Minor.Patch format (e.g. 1.45.320).",
        )


def parse_dependency_pin(raw: str) -> DependencyPin:
    package, separator, version = (raw or "").partition("=")
    package = package.strip()
    version = version.strip()
    if separator != "=" or not package or not version:
        raise ReleasePrepError(
            "Invalid dependency pin",
            "Dependency pins must use PACKAGE=Major.Minor.Patch.",
        )
    if not VERSION_PATTERN.match(version):
        raise ReleasePrepError(
            "Invalid dependency pin version",
            f"{package} dependency version must follow Major.Minor.Patch.",
        )
    return DependencyPin(package=package, version=version)


def find_previous_tag(repo: Path, release_tag: str) -> str | None:
    tags = run_git(repo, ["tag", "--merged", "HEAD", "--sort=-v:refname"]).splitlines()
    for tag in tags:
        normalized = tag.strip()
        if VERSION_PATTERN.match(normalized) and normalized != release_tag:
            return normalized
    return None


def read_commits(repo: Path, previous_tag: str | None) -> list[Commit]:
    revision = f"{previous_tag}..HEAD" if previous_tag else "HEAD"
    raw = run_git(repo, ["log", "--format=%H%x00%s%x00%b%x1e", revision])
    commits: list[Commit] = []
    for record in raw.split("\x1e"):
        record = record.rstrip("\n")
        if not record:
            continue
        parts = record.split("\x00", 2)
        if len(parts) != 3:
            continue
        commits.append(Commit(sha=parts[0], subject=parts[1], body=parts[2].strip()))
    return commits


BUMP_RANK = {"patch": 1, "minor": 2, "major": 3}
BUMP_BY_TYPE = {"feat": "minor", "fix": "patch", "perf": "patch"}


def max_bump(*bumps: str | None) -> str | None:
    ranked = [bump for bump in bumps if bump]
    return max(ranked, key=lambda bump: BUMP_RANK[bump]) if ranked else None


def classify_bump(commits: list[Commit]) -> str | None:
    """Return the semver bump implied by conventional commits, or None when nothing is releasable.

    A breaking change bumps major, feat bumps minor, fix/perf bump patch. chore, docs, refactor,
    test and the bot's chore(release) commits do not trigger a release on their own.
    """
    bump = None
    for commit in commits:
        match = CONVENTIONAL_PATTERN.match(commit.subject)
        if not match:
            continue
        commit_type, _, bang, _ = match.groups()
        if bang or BREAKING_PATTERN.search(commit.body):
            bump = max_bump(bump, "major")
        else:
            bump = max_bump(bump, BUMP_BY_TYPE.get(commit_type.lower()))
    return bump


def bump_version(version: str | None, bump: str) -> str:
    """Apply a bump to Major.Minor.Patch. A repo with no previous tag starts at 1.0.0."""
    if version is None:
        return "1.0.0"
    validate_tag(version)
    major, minor, patch = (int(part) for part in version.split("."))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ReleasePrepError("Invalid version bump", f"Unknown bump '{bump}'.")


def latest_version_tag(repo: Path) -> str | None:
    """Highest Major.Minor.Patch tag reachable from HEAD."""
    return find_previous_tag(repo, "")


def commits_since_latest_tag(repo: Path) -> tuple[str | None, list[Commit]]:
    previous_tag = latest_version_tag(repo)
    return previous_tag, read_commits(repo, previous_tag)


def _first_line(value: str) -> str:
    return (value or "").strip().splitlines()[0].strip()


def _entry_text(scope: str | None, description: str, sha: str) -> str:
    text = description.strip()
    if scope:
        text = f"{scope.strip()}: {text}"
    return f"{text} ({sha[:7]})"


def parse_commits(commits: list[Commit]) -> list[ChangelogEntry]:
    entries: list[ChangelogEntry] = []
    for commit in commits:
        match = CONVENTIONAL_PATTERN.match(commit.subject)
        if not match:
            continue

        commit_type, scope, bang, description = match.groups()
        commit_type = commit_type.lower()
        breaking_match = BREAKING_PATTERN.search(commit.body)
        if bang or breaking_match:
            breaking_text = _first_line(breaking_match.group(1)) if breaking_match else description
            entries.append(ChangelogEntry("Breaking Changes", _entry_text(scope, breaking_text, commit.sha)))

        section = SECTION_TITLES.get(commit_type)
        if section:
            entries.append(ChangelogEntry(section, _entry_text(scope, description, commit.sha)))

    return entries


def render_section(tag: str, release_date: date, entries: list[ChangelogEntry], allow_empty: bool) -> str:
    grouped: dict[str, list[str]] = {}
    for entry in entries:
        grouped.setdefault(entry.section, []).append(entry.text)

    if not grouped:
        if not allow_empty:
            raise ReleasePrepError(
                "Empty changelog",
                "No visible conventional commits found. Pass --allow-empty to release without user-facing changes.",
            )
        grouped["Changed"] = ["No user-facing changes."]

    lines = [f"## [{tag}] - {release_date.isoformat()}", ""]
    for section in SECTION_ORDER:
        items = grouped.get(section)
        if not items:
            continue
        lines.append(f"### {section}")
        lines.append("")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def update_changelog(content: str, tag: str, section: str) -> str:
    marker = re.search(r"^## \[Unreleased\]\s*$", content, re.MULTILINE)
    if not marker:
        raise ReleasePrepError("Missing changelog section", "CHANGELOG.md must contain '## [Unreleased]'.")

    insert_at = marker.end()
    tail = content[insert_at:].lstrip()
    return content[:insert_at].rstrip() + "\n\n" + section.rstrip() + "\n\n" + tail


def find_changelog_section(content: str, tag: str) -> str | None:
    marker = re.search(rf"^## \[{re.escape(tag)}\](?:\s|-).*$", content, re.MULTILINE)
    if not marker:
        return None

    next_marker = re.search(r"^## \[[^\]]+\](?:\s|-).*$", content[marker.end():], re.MULTILINE)
    end = marker.end() + next_marker.start() if next_marker else len(content)
    return content[marker.start():end].strip() + "\n"


def update_thunderstore_config(content: str, tag: str) -> str:
    updated, count = VERSION_LINE_PATTERN.subn(rf'\g<1>"{tag}"', content, count=1)
    if count != 1:
        raise ReleasePrepError(
            "Missing Thunderstore version",
            "thunderstore.toml must contain exactly one package versionNumber line.",
        )
    return updated


def update_dependency_pins(content: str, pins: list[DependencyPin]) -> str:
    updated = content
    for pin in pins:
        matches = [
            ("string", match)
            for match in DEPENDENCY_STRING_PATTERN.finditer(updated)
            if match.group("package") == pin.package
        ]
        matches.extend(
            ("table", match)
            for match in DEPENDENCY_TABLE_PATTERN.finditer(updated)
            if match.group("package") == pin.package
        )
        if not matches:
            raise ReleasePrepError(
                "Missing dependency pin",
                f"thunderstore.toml does not contain dependency '{pin.package}'.",
            )
        if len(matches) > 1:
            raise ReleasePrepError(
                "Duplicate dependency pin",
                f"thunderstore.toml contains dependency '{pin.package}' more than once.",
            )

        kind, match = matches[0]
        if kind == "string":
            replacement = f'{match.group("quote")}{pin.package}-{pin.version}{match.group("quote")}'
        else:
            replacement = f'{match.group("prefix")}{match.group("quote")}{pin.version}{match.group("quote")}'
        updated = updated[:match.start()] + replacement + updated[match.end():]
    return updated


def prepare_release(
    repo: Path,
    tag: str,
    changelog_path: Path,
    thunderstore_path: Path,
    release_notes_path: Path | None,
    dependency_pins: list[DependencyPin],
    allow_empty: bool,
    release_date: date,
) -> None:
    validate_tag(tag)
    previous_tag = None
    entries: list[ChangelogEntry] = []
    changelog = changelog_path.read_text(encoding="utf-8")
    existing_section = find_changelog_section(changelog, tag)
    if existing_section is None:
        previous_tag = find_previous_tag(repo, tag)
        commits = read_commits(repo, previous_tag)
        entries = parse_commits(commits)
        section = render_section(tag, release_date, entries, allow_empty)
        changelog = update_changelog(changelog, tag, section)
        changelog_path.write_text(changelog, encoding="utf-8", newline="\n")
    else:
        section = existing_section

    if release_notes_path is not None:
        release_notes_path.parent.mkdir(parents=True, exist_ok=True)
        release_notes_path.write_text(section, encoding="utf-8", newline="\n")

    thunderstore = thunderstore_path.read_text(encoding="utf-8")
    thunderstore = update_thunderstore_config(thunderstore, tag)
    thunderstore = update_dependency_pins(thunderstore, dependency_pins)
    thunderstore_path.write_text(thunderstore, encoding="utf-8", newline="\n")

    print(f"Prepared release {tag}")
    if existing_section is None:
        print(f"Previous tag: {previous_tag or '(none)'}")
        print(f"Changelog entries: {len(entries)}")
    else:
        print("Existing changelog section reused.")
    print(f"Dependency pins: {len(dependency_pins)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Release version tag.")
    parser.add_argument("--repo-root", default=".", help="Package repo root. Defaults to current directory.")
    parser.add_argument("--changelog", default="CHANGELOG.md", help="Changelog path relative to repo root.")
    parser.add_argument("--thunderstore-config", default="thunderstore.toml", help="Thunderstore config path.")
    parser.add_argument(
        "--release-notes-output",
        default=None,
        help="Optional path, relative to repo root, where the generated changelog section is written.",
    )
    parser.add_argument(
        "--pin-dependency",
        action="append",
        default=[],
        metavar="PACKAGE=VERSION",
        help="Update a matching Thunderstore dependency pin to the supplied version.",
    )
    parser.add_argument("--allow-empty", action="store_true", help="Allow releases with no visible changelog entries.")
    parser.add_argument("--date", default=None, help="Release date override in YYYY-MM-DD form, for tests/backfills.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo = Path(args.repo_root).resolve()
    release_date = date.fromisoformat(args.date) if args.date else date.today()

    try:
        dependency_pins = [parse_dependency_pin(raw) for raw in args.pin_dependency]
        prepare_release(
            repo=repo,
            tag=args.tag,
            changelog_path=repo / args.changelog,
            thunderstore_path=repo / args.thunderstore_config,
            release_notes_path=(repo / args.release_notes_output) if args.release_notes_output else None,
            dependency_pins=dependency_pins,
            allow_empty=args.allow_empty,
            release_date=release_date,
        )
        return 0
    except ReleasePrepError as exc:
        print(f"::error title={exc.title}::{exc.message}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"::error title=Git failed::git exited with {exc.returncode}", file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
