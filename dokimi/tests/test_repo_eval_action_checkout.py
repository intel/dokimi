# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""repo-eval.yml: the setup action is checked out from its own repository, at dokimi-ref.

Every job that uses the setup action checks it out first, into `.dokimi-action`. The checkout
takes its repository from `dokimi-action-repository` (default intel/dokimi), not from the
`dokimi-repository` that selects the package, and its ref from `dokimi-ref`, the commit the
caller pins in `uses:`. `job.workflow_sha` is not a context property, so it evaluated to
an empty ref. The self-check in repo-eval-self.yml passes its own repository, so a fork
still checks out the commit it runs.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REPO_EVAL = ROOT / ".github/workflows/repo-eval.yml"
REPO_EVAL_SELF = ROOT / ".github/workflows/repo-eval-self.yml"
CHECKOUT_PATH = ".dokimi-action"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _action_checkouts(workflow: dict) -> list[tuple[str, dict]]:
    found = []
    for job_id, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("actions/checkout@") and step.get("with", {}).get("path") == CHECKOUT_PATH:
                found.append((job_id, step["with"]))
    return found


def test_the_workflow_declares_the_action_repository_input_with_its_default() -> None:
    inputs = _load(REPO_EVAL)[True]["workflow_call"]["inputs"]
    assert "dokimi-action-repository" in inputs
    assert inputs["dokimi-action-repository"]["default"] == "intel/dokimi"


def test_every_action_checkout_takes_its_repository_from_dokimi_action_repository() -> None:
    checkouts = _action_checkouts(_load(REPO_EVAL))
    assert len(checkouts) == 3, checkouts
    for job_id, with_ in checkouts:
        assert with_["repository"] == "${{ inputs.dokimi-action-repository }}", job_id


def test_validate_checks_the_action_out_at_dokimi_ref_and_records_its_commit() -> None:
    workflow = _load(REPO_EVAL)
    checkouts = dict(_action_checkouts(workflow))
    assert checkouts["validate"]["ref"] == "${{ steps.action-ref.outputs.sha }}"
    assert workflow["jobs"]["validate"]["outputs"]["action-sha"] == "${{ steps.action.outputs.sha }}"
    recorder = [s for s in workflow["jobs"]["validate"]["steps"] if s.get("id") == "action"]
    assert recorder and recorder[0]["run"] == 'echo "sha=$(git -C .dokimi-action rev-parse HEAD)" >> "$GITHUB_OUTPUT"'


def test_downstream_jobs_check_the_action_out_at_the_commit_validate_recorded() -> None:
    # A tag in dokimi-ref may move while validate runs; the package SHA does not name the action.
    checkouts = dict(_action_checkouts(_load(REPO_EVAL)))
    for job_id in ("scan", "tasks-check"):
        assert checkouts[job_id]["ref"] == "${{ needs.validate.outputs.action-sha }}", job_id


def test_job_workflow_sha_is_not_used() -> None:
    assert "job.workflow_sha" not in REPO_EVAL.read_text(encoding="utf-8")


def test_the_self_check_passes_its_own_repository_as_the_action_repository() -> None:
    caller = _load(REPO_EVAL_SELF)["jobs"]["self"]["with"]
    assert caller["dokimi-action-repository"] == "${{ github.repository }}"


def _validate_steps() -> list[dict]:
    return _load(REPO_EVAL)["jobs"]["validate"]["steps"]


def test_validate_checks_the_action_out_at_a_commit_resolved_before_the_checkout() -> None:
    # A branch named like a tag would win a bare ref in actions/checkout: the commit is resolved first.
    steps = _validate_steps()
    resolve = next(s for s in steps if s.get("id") == "action-ref")
    checkout = next(s for s in steps if s.get("name") == "Check out the setup action")
    assert steps.index(resolve) < steps.index(checkout)
    assert checkout["with"]["ref"] == "${{ steps.action-ref.outputs.sha }}"
    assert "ls-remote --tags" in resolve["run"] and "^{}" in resolve["run"]


def test_the_setup_action_commit_resolver_takes_a_tag_as_its_peeled_commit(tmp_path: Path) -> None:
    # Runs the step as the runner does, against a fake `git ls-remote` that lists a tag and its peeled commit.
    import os
    import subprocess

    resolve = next(s for s in _validate_steps() if s.get("id") == "action-ref")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text("#!/bin/sh\n"
                        "printf '%s\\n' '1111111111111111111111111111111111111111\trefs/tags/release' "
                        "'2222222222222222222222222222222222222222\trefs/tags/release^{}'\n", encoding="utf-8")
    fake_git.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text(resolve["run"], encoding="utf-8")

    def resolve_ref(ref: str) -> str:
        out = tmp_path / "output"
        out.write_text("", encoding="utf-8")
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "GITHUB_OUTPUT": str(out),
               "REF": ref, "ACTION_REPO": "intel/dokimi"}
        result = subprocess.run(["bash", "-e", "-o", "pipefail", str(script)], env=env,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return out.read_text(encoding="utf-8")

    assert resolve_ref("release") == "sha=2222222222222222222222222222222222222222\n"
    assert resolve_ref("3" * 40) == f"sha={'3' * 40}\n"
