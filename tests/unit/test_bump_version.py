"""Regression tests for protected-branch-safe release preparation."""

from pathlib import Path

import pytest

from scripts import bump_version


def commit(subject: str, body: str = "", sha: str = "01234567") -> dict[str, str]:
    """Build a commit record accepted by the release classifier."""
    return {"sha": sha, "subject": subject, "body": body}


@pytest.mark.parametrize(
    ("commits", "expected"),
    [
        ([commit("feat!: replace the public API")], "major"),
        ([commit("fix: preserve behavior", "BREAKING CHANGE: incompatible schema")], "major"),
        ([commit("feat(search): add provider")], "minor"),
        ([commit("fix(auth): reject an invalid token")], "patch"),
        ([commit("ci: protect main")], "none"),
    ],
)
def test_classify_commits_uses_highest_semantic_change(
    commits: list[dict[str, str]], expected: str
) -> None:
    """Classify Conventional Commits without treating ordinary prose as breaking."""
    bump_type, *_ = bump_version.classify_commits(commits)
    assert bump_type == expected


def test_breaking_change_must_start_a_line() -> None:
    """Do not infer a major release from incidental body text."""
    commits = [
        commit(
            "fix: clarify documentation",
            "This sentence discusses a BREAKING CHANGE: without declaring one.",
        )
    ]
    bump_type, *_ = bump_version.classify_commits(commits)
    assert bump_type == "patch"


def test_get_last_tag_ignores_unreachable_and_non_semver_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Select only an exact semantic-version tag from the reachable-tag query."""
    monkeypatch.setattr(
        bump_version,
        "run",
        lambda command, check=False: "v7.5.0-prod\nv7.1.5\nrelease-candidate",
    )
    assert bump_version.get_last_tag() == "v7.1.5"


def test_prepare_helpers_update_only_release_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Update version, changelog, and compose content without Git mutations."""
    version_file = tmp_path / "VERSION"
    changelog_file = tmp_path / "CHANGELOG.md"
    compose_file = tmp_path / "docker-compose.staging.yml"
    version_file.write_text("7.1.5\n")
    changelog_file.write_text("# Changelog\n")
    compose_file.write_text("environment:\n  APP_VERSION: 7.1.5\n")

    monkeypatch.setattr(bump_version, "VERSION_FILE", version_file)
    monkeypatch.setattr(bump_version, "CHANGELOG_FILE", changelog_file)
    monkeypatch.setattr(bump_version, "COMPOSE_FILE", compose_file)

    bump_version.write_version(7, 1, 6)
    bump_version.update_changelog(
        "7.1.6",
        [],
        [],
        [commit("fix: restore canonical recovery")],
    )
    bump_version.update_compose_version("7.1.6")

    assert version_file.read_text() == "7.1.6\n"
    assert changelog_file.read_text().startswith("## [7.1.6]")
    assert "fix: restore canonical recovery" in changelog_file.read_text()
    assert "APP_VERSION: 7.1.6" in compose_file.read_text()
