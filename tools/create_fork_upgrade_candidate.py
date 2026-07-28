#!/usr/bin/env python3
"""Create a guarded fork PR when CoplayDev publishes a new stable release."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".github" / "fork-patches.json"
CANDIDATE_BRANCH = "automation/upstream-stable"
VALIDATION_WORKFLOWS = (
    "python-tests.yml",
    "unity-tests.yml",
    "e2e-bridge.yml",
)


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def gh_json(*args: str) -> Any:
    return json.loads(run("gh", *args, capture=True))


def git_ref_exists(ref: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"Unable to inspect Git ref {ref}")
    return result.returncode == 0


def git_is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            f"Unable to compare Git ancestry: {ancestor} -> {descendant}"
        )
    return result.returncode == 0


def candidate_branch_changed(remote: str, branch: str, release_tag: str) -> bool:
    remote_ref = f"refs/remotes/{remote}/{branch}"
    if not git_ref_exists(remote_ref):
        return True
    local_tree = run("git", "rev-parse", "HEAD^{tree}", capture=True)
    remote_tree = run("git", "rev-parse", f"{remote_ref}^{{tree}}", capture=True)
    return (
        local_tree != remote_tree
        or not git_is_ancestor(f"refs/tags/{release_tag}", remote_ref)
    )


def ensure_candidate_validation(
    repository: str,
    branch: str,
    commit: str,
) -> None:
    for workflow in VALIDATION_WORKFLOWS:
        runs = gh_json(
            "run",
            "list",
            "--repo",
            repository,
            "--workflow",
            workflow,
            "--branch",
            branch,
            "--commit",
            commit,
            "--event",
            "workflow_dispatch",
            "--limit",
            "1",
            "--json",
            "databaseId,status,conclusion,url",
        )
        if runs:
            print(
                f"{workflow} already has a workflow_dispatch run for {commit[:12]}"
            )
            continue

        run(
            "gh",
            "workflow",
            "run",
            workflow,
            "--repo",
            repository,
            "--ref",
            branch,
        )
        print(f"Dispatched {workflow} for {branch}")


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as stream:
        config = json.load(stream)
    if config.get("version") != 1:
        raise RuntimeError("Unsupported fork patch manifest version")
    return config


def patch_is_in_release(
    upstream_repository: str,
    upstream_pr: int,
    release_tag: str,
) -> bool:
    pull = gh_json(
        "api",
        f"repos/{upstream_repository}/pulls/{upstream_pr}",
    )
    merge_commit = pull.get("merge_commit_sha")
    if not pull.get("merged_at") or not merge_commit:
        return False

    comparison = gh_json(
        "api",
        f"repos/{upstream_repository}/compare/{merge_commit}...{release_tag}",
    )
    return comparison.get("status") in {"ahead", "identical"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", "liuzqk/unity-mcp"),
    )
    parser.add_argument(
        "--remote",
        default=os.environ.get("FORK_REMOTE", "origin"),
    )
    args = parser.parse_args()

    config = load_config()
    upstream = config["upstream_repository"]
    stable_branch = config["stable_branch"]
    release = gh_json("api", f"repos/{upstream}/releases/latest")
    tag = release["tag_name"]

    active_patches: list[dict[str, Any]] = []
    for patch in config["patches"]:
        absorbed = patch_is_in_release(
            upstream,
            int(patch["upstream_pr"]),
            tag,
        )
        print(
            f"{patch['name']}: "
            f"{'absorbed by release' if absorbed else 'replay required'}"
        )
        if not absorbed:
            commits = gh_json(
                "api",
                f"repos/{upstream}/pulls/{patch['upstream_pr']}/commits?per_page=100",
            )
            if not commits:
                raise RuntimeError(
                    f"Upstream PR #{patch['upstream_pr']} has no commits"
                )
            active_patches.append(
                {
                    **patch,
                    "commits": [commit["sha"] for commit in commits],
                }
            )

    print(f"Latest stable release: {tag}")
    if args.dry_run:
        return

    run("git", "config", "user.name", "github-actions[bot]")
    run(
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    remotes = run("git", "remote", capture=True).splitlines()
    if "upstream" not in remotes:
        run("git", "remote", "add", "upstream", f"https://github.com/{upstream}.git")
    else:
        run("git", "remote", "set-url", "upstream", f"https://github.com/{upstream}.git")

    run("git", "fetch", "upstream", "tag", tag, "--force")
    run(
        "git",
        "fetch",
        args.remote,
        f"+refs/heads/*:refs/remotes/{args.remote}/*",
    )

    stable_ref = f"refs/remotes/{args.remote}/{stable_branch}"
    run("git", "rev-parse", "--verify", stable_ref)
    deployed = subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            f"refs/tags/{tag}",
            stable_ref,
        ],
        cwd=ROOT,
        check=False,
    )
    if deployed.returncode == 0:
        print(f"{tag} is already contained in {stable_branch}; nothing to do")
        return
    if deployed.returncode != 1:
        raise RuntimeError("Unable to compare upstream release with the stable branch")

    candidate_branch = CANDIDATE_BRANCH
    open_prs = gh_json(
        "pr",
        "list",
        "--repo",
        args.repository,
        "--state",
        "open",
        "--head",
        candidate_branch,
        "--json",
        "number",
    )
    if open_prs:
        print(f"Candidate PR already open: #{open_prs[0]['number']}")

    run("git", "checkout", "-B", candidate_branch, f"refs/tags/{tag}")
    for patch in active_patches:
        for commit in patch["commits"]:
            run("git", "cherry-pick", commit)

    candidate_changed = candidate_branch_changed(
        args.remote,
        candidate_branch,
        tag,
    )
    if candidate_changed:
        run(
            "git",
            "push",
            "--force-with-lease",
            "--set-upstream",
            args.remote,
            candidate_branch,
        )
    else:
        print("Candidate branch tree is unchanged; skipping force-push")

    patch_lines = "\n".join(
        f"- {patch['name']} (upstream #{patch['upstream_pr']}, "
        f"{len(patch['commits'])} commit(s))"
        for patch in active_patches
    )
    if not patch_lines:
        patch_lines = "- none; all tracked patches are included upstream"
    body = (
        f"Automated candidate for upstream stable `{tag}`.\n\n"
        "Unreleased patches replayed:\n\n"
        f"{patch_lines}\n\n"
        "This PR never updates production project dependencies. Merge only after "
        "the fork's Python, Unity, and multi-Editor gates pass. The automation "
        "explicitly dispatches branch validation because workflow-token pushes do "
        "not trigger downstream runs and token-created PR runs may require approval."
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".md",
        delete=False,
    ) as stream:
        stream.write(body)
        body_path = stream.name
    try:
        if open_prs:
            if candidate_changed:
                run(
                    "gh",
                    "pr",
                    "edit",
                    str(open_prs[0]["number"]),
                    "--repo",
                    args.repository,
                    "--title",
                    f"Upgrade internal stable base to {tag}",
                    "--body-file",
                    body_path,
                )
        else:
            run(
                "gh",
                "pr",
                "create",
                "--repo",
                args.repository,
                "--base",
                stable_branch,
                "--head",
                candidate_branch,
                "--title",
                f"Upgrade internal stable base to {tag}",
                "--body-file",
                body_path,
            )
    finally:
        Path(body_path).unlink(missing_ok=True)

    candidate_commit = run(
        "git",
        "rev-parse",
        "HEAD" if candidate_changed else f"refs/remotes/{args.remote}/{candidate_branch}",
        capture=True,
    )
    ensure_candidate_validation(
        args.repository,
        candidate_branch,
        candidate_commit,
    )


if __name__ == "__main__":
    main()
