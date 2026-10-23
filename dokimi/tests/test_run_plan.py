# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""plan.v1 / result.v1 and the completeness check that refuses a partial run."""

from __future__ import annotations

import copy

import pytest

from dokimi.artifacts import ArtifactValidationError, load_artifact, save_artifact
from dokimi.runner.plan import cell_id, completeness, plan_cells, plan_digest


def _plan(tmp_path, **over):
    cells = plan_cells(["no-skill", "skill"], ["m1"], ["t1", "t2", "t3"])
    plan = {"subjects": [{"id": "skill:x", "sha": None, "fingerprint": None}],
            "suites": [{"id": "s", "hash": "sha256:00", "reasons": ["SKILL.md changed"]}],
            "models": ["m1"], "harnesses": ["docker-claude"], "grader": {"id": "pytest", "digest": None},
            "varied_variable": "skill", "cells": cells, "budget": {"cells": len(cells)}, **over}
    return save_artifact("plan", plan, tmp_path / "plan.json")


def _result(plan, statuses, **over):
    cells = [{"cell_id": c["cell_id"], "status": s} for c, s in zip(plan["cells"], statuses, strict=True) if s]
    return {"plan_digest": plan_digest(plan), "cells": cells, **over}


def test_cell_ids_are_stable_and_distinct_per_coordinate():
    assert cell_id("a", "m", "t", 1) == cell_id("a", "m", "t", 1)
    ids = {cell_id(*c) for c in [("a", "m", "t", 1), ("a", "m", "t", 2), ("a", None, "t", 1),
                                 ("b", "m", "t", 1), ("a", "m", "u", 1)]}
    assert len(ids) == 5


def test_plan_cells_is_the_full_grid():
    cells = plan_cells(["a", "b"], ["m1", "m2"], ["t1", "t2", "t3"], reps=3)
    assert len(cells) == 36
    assert len({c["cell_id"] for c in cells}) == 36
    assert cells[1] == {"cell_id": cell_id("a", "m1", "t1", 2), "arm": "a", "model": "m1", "task": "t1", "rep": 2}
    with pytest.raises(ValueError):
        plan_cells(["a"], ["m"], ["t"], reps=0)


def test_two_of_six_cells_is_incomplete_even_when_both_pass(tmp_path):
    plan = _plan(tmp_path)
    found = completeness(plan, _result(plan, ["pass", "pass", None, None, None, None]))
    assert (found.state, found.planned, found.measured) == ("incomplete", 6, 2)
    assert found.missing == tuple(c["cell_id"] for c in plan["cells"][2:])
    assert found.statuses == {"pass": 2, "not_run": 4}
    assert not found.complete


@pytest.mark.parametrize("unmeasured", ["not_run", "infra_error", "invalid"])
def test_a_recorded_but_unmeasured_cell_keeps_the_run_incomplete(tmp_path, unmeasured):
    plan = _plan(tmp_path)
    found = completeness(plan, _result(plan, ["pass"] * 5 + [unmeasured]))
    assert found.state == "incomplete"
    assert found.missing == (plan["cells"][5]["cell_id"],)


def test_every_cell_measured_is_complete_whatever_the_verdicts(tmp_path):
    plan = _plan(tmp_path)
    found = completeness(plan, _result(plan, ["pass", "fail", "inconclusive", "fail", "fail", "pass"]))
    assert found.complete
    assert found.as_dict()["statuses"] == {"pass": 2, "fail": 3, "inconclusive": 1}


def test_a_result_for_another_plan_is_invalid(tmp_path):
    plan = _plan(tmp_path)
    result = _result(plan, ["pass"] * 6)
    edited = copy.deepcopy(plan)
    edited["grader"]["digest"] = "sha256:ff"  # the grader changed after the run
    found = completeness(edited, result)
    assert found.state == "invalid"
    assert "not " + plan_digest(edited) in found.problems[0]


def test_the_digest_ignores_generated_at_only(tmp_path):
    plan = _plan(tmp_path)
    assert plan_digest({**plan, "generated_at": "later"}) == plan_digest(plan)
    assert plan_digest({**plan, "varied_variable": ["code", "guidance"]}) != plan_digest(plan)


def test_unplanned_or_doubled_cells_are_invalid(tmp_path):
    plan = _plan(tmp_path)
    extra = _result(plan, ["pass"] * 6)
    extra["cells"].append({"cell_id": cell_id("other", "m1", "t1", 1), "status": "pass"})
    assert completeness(plan, extra).state == "invalid"
    doubled = _result(plan, ["pass"] * 6)
    doubled["cells"].append({"cell_id": plan["cells"][0]["cell_id"], "status": "fail"})
    assert "more than one status" in completeness(plan, doubled).problems[0]


def test_artifacts_round_trip_and_reject_an_unknown_status(tmp_path):
    plan = _plan(tmp_path)
    assert load_artifact("plan", tmp_path / "plan.json") == plan
    result = _result(plan, ["pass"] * 6, execution_id="exec-1")
    save_artifact("result", result, tmp_path / "result.json")
    assert completeness(load_artifact("plan", tmp_path / "plan.json"),
                        load_artifact("result", tmp_path / "result.json")).complete
    result["cells"][0]["status"] = "skipped"
    with pytest.raises(ArtifactValidationError):
        save_artifact("result", result, tmp_path / "bad.json")


def test_a_plan_with_two_free_variables_needs_the_tuple_form(tmp_path):
    _plan(tmp_path, varied_variable=["code", "guidance"])
    with pytest.raises(ArtifactValidationError):
        _plan(tmp_path, varied_variable=[])


def test_an_empty_plan_is_incomplete_not_complete():
    empty = {"cells": []}
    got = completeness(empty, {"plan_digest": plan_digest(empty), "cells": []})
    assert got.state == "incomplete"
    assert not got.complete


def test_a_plan_that_repeats_a_cell_is_invalid(tmp_path):
    plan = _plan(tmp_path)
    plan["cells"] = [*plan["cells"], plan["cells"][0]]
    got = completeness(plan, _result(plan, ["pass"] * 7))
    assert got.state == "invalid"
    assert "plan repeats" in got.problems[0]


def test_planned_counts_distinct_cells_when_the_plan_repeats_one():
    cell = {"cell_id": "c-1"}
    plan = {"cells": [cell, cell]}
    got = completeness(plan, {"plan_digest": plan_digest(plan), "cells": []})
    assert got.planned == 1


@pytest.mark.parametrize("bad", [{"cell_id": "c-1"}, {"status": "pass"}, {"cell_id": "c-1", "status": ["pass"]},
                                 {"cell_id": "c-1", "status": "PASS"}, {"cell_id": 3, "status": "pass"}])
def test_a_malformed_result_cell_is_invalid_not_an_exception(tmp_path, bad):
    plan = _plan(tmp_path)
    result = _result(plan, ["pass"] * 6)
    result["cells"].append(bad)
    got = completeness(plan, result)
    assert got.state == "invalid"
    assert got.problems


def test_a_plan_cell_without_an_id_is_invalid():
    plan = {"cells": [{"arm": "a"}]}
    got = completeness(plan, {"plan_digest": plan_digest(plan), "cells": []})
    assert got.state == "invalid"


def test_an_invalid_result_reports_no_measured_cells(tmp_path):
    plan = _plan(tmp_path)
    got = completeness(plan, _result(plan, ["pass"] * 6, plan_digest="sha256:other"))
    assert got.state == "invalid"
    assert got.measured == 0


@pytest.mark.parametrize("bad", [{"status": "PASS"}, {"status": ["pass"]}, {}])
def test_a_bad_status_on_a_planned_cell_is_invalid(tmp_path, bad):
    plan = _plan(tmp_path)
    result = _result(plan, ["pass"] * 6)
    result["cells"][0] = {"cell_id": plan["cells"][0]["cell_id"], **bad}
    got = completeness(plan, result)
    assert (got.state, got.measured) == ("invalid", 0)
    assert any("unknown status" in p for p in got.problems)


@pytest.mark.parametrize("bad", ["junk", {"cell_id": 3, "status": "pass"}])
def test_a_result_cell_without_a_string_id_is_reported(tmp_path, bad):
    plan = _plan(tmp_path)
    result = _result(plan, ["pass"] * 6)
    result["cells"].append(bad)
    got = completeness(plan, result)
    assert got.state == "invalid"
    assert any("without a string cell_id" in p for p in got.problems)


def test_result_cells_that_are_not_a_list_do_not_raise(tmp_path):
    plan = _plan(tmp_path)
    assert not completeness(plan, {"plan_digest": plan_digest(plan), "cells": None}).complete
