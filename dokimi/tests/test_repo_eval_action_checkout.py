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
    assert checkouts["validate"]["ref"] == "${{ inputs.dokimi-ref }}"
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
