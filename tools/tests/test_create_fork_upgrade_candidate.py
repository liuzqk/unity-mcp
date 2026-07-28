from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import create_fork_upgrade_candidate as candidate  # noqa: E402


def test_candidate_branch_changed_when_remote_ref_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(candidate, "git_ref_exists", lambda _ref: False)

    assert candidate.candidate_branch_changed(
        "origin",
        candidate.CANDIDATE_BRANCH,
        "v10.2.0",
    )


def test_candidate_branch_uses_tree_equality_to_avoid_republishing(
    monkeypatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, capture: bool = False) -> str:
        calls.append(args)
        assert capture
        return "same-tree"

    monkeypatch.setattr(candidate, "git_ref_exists", lambda _ref: True)
    monkeypatch.setattr(candidate, "git_is_ancestor", lambda _left, _right: True)
    monkeypatch.setattr(candidate, "run", fake_run)

    assert not candidate.candidate_branch_changed(
        "origin",
        candidate.CANDIDATE_BRANCH,
        "v10.2.0",
    )
    assert calls == [
        ("git", "rev-parse", "HEAD^{tree}"),
        (
            "git",
            "rev-parse",
            "refs/remotes/origin/automation/upstream-stable^{tree}",
        ),
    ]


def test_candidate_branch_refreshes_when_tree_changes(monkeypatch) -> None:
    trees = iter(("local-tree", "remote-tree"))
    monkeypatch.setattr(candidate, "git_ref_exists", lambda _ref: True)
    monkeypatch.setattr(candidate, "git_is_ancestor", lambda _left, _right: True)
    monkeypatch.setattr(
        candidate,
        "run",
        lambda *_args, capture=False: next(trees),
    )

    assert candidate.candidate_branch_changed(
        "origin",
        candidate.CANDIDATE_BRANCH,
        "v10.2.0",
    )


def test_candidate_branch_refreshes_for_new_release_with_same_tree(
    monkeypatch,
) -> None:
    monkeypatch.setattr(candidate, "git_ref_exists", lambda _ref: True)
    monkeypatch.setattr(candidate, "git_is_ancestor", lambda _left, _right: False)
    monkeypatch.setattr(
        candidate,
        "run",
        lambda *_args, capture=False: "same-tree",
    )

    assert candidate.candidate_branch_changed(
        "origin",
        candidate.CANDIDATE_BRANCH,
        "v10.2.0",
    )


def test_dispatches_missing_candidate_validation_workflows(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, capture: bool = False) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(candidate, "gh_json", lambda *_args: [])
    monkeypatch.setattr(candidate, "run", fake_run)

    candidate.ensure_candidate_validation(
        "liuzqk/unity-mcp",
        candidate.CANDIDATE_BRANCH,
        "abc123",
    )

    assert [call[3] for call in calls] == list(candidate.VALIDATION_WORKFLOWS)
    assert all(
        call[-2:] == ("--ref", candidate.CANDIDATE_BRANCH)
        for call in calls
    )


def test_keeps_existing_candidate_validation_run(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        candidate,
        "gh_json",
        lambda *_args: [{"databaseId": 1, "status": "completed"}],
    )
    monkeypatch.setattr(
        candidate,
        "run",
        lambda *args, capture=False: calls.append(args) or "",
    )

    candidate.ensure_candidate_validation(
        "liuzqk/unity-mcp",
        candidate.CANDIDATE_BRANCH,
        "abc123",
    )

    assert calls == []
