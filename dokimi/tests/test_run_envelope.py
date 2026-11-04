# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The run envelope (#277, B1): ``run_id`` on run artifacts, optional for every reader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.artifacts import (
    RUN_KINDS,
    SCHEMA_DIR,
    SCHEMA_VERSIONS,
    ArtifactValidationError,
    current_run_id,
    load_artifact,
    save_artifact,
    stamp,
)
from dokimi.runner.plan import plan_digest
from dokimi.runs import scan as scan_root

#: Schemas that were closed at top level and so had to name ``run_id`` to admit it.
WIDENED = ("repo_readiness", "tool_preference_groups", "harness_profile", "repo_manifest")


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "artifacts"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _ledger() -> dict:
    """A valid repo_ledger.v1 payload, without its envelope."""
    data = _read(FIXTURES / "repo_ledger.v1.json")
    return {k: v for k, v in data.items() if k not in ("schema_version", "run_id")}


def _questions() -> dict:
    return {"questions": [{"id": "q1", "question": "How do I install it?"}]}


def test_input_and_generated_kinds_are_not_run_kinds():
    assert RUN_KINDS == frozenset(SCHEMA_VERSIONS) - {"dokimi_config", "cli_routes", "questions"}
    assert {"dokimi_config", "cli_routes", "questions"}.isdisjoint(RUN_KINDS)


def test_a_question_set_is_an_input_and_carries_no_run_id(tmp_path):
    path = tmp_path / "questions.json"
    written = save_artifact("questions", _questions(), path)
    assert "run_id" not in written
    assert list(_read(path)) == ["schema_version", "questions"]


def test_re_saving_a_question_set_keeps_its_hash(tmp_path, monkeypatch):
    """scorecard's question_set_hash digests the file's bytes, so a re-save in another run keeps them."""
    from dokimi.subjects.scorecard import _digest_values

    path = tmp_path / "questions.json"
    monkeypatch.setattr("dokimi.artifacts.current_run_id", lambda: "20260101T000000Z-aaaaaaaa")
    save_artifact("questions", _questions(), path)
    before = _digest_values((str(path),))
    monkeypatch.setattr("dokimi.artifacts.current_run_id", lambda: "20260102T000000Z-bbbbbbbb")
    save_artifact("questions", _questions(), path)
    assert _digest_values((str(path),)) == before


def test_a_question_set_written_with_a_run_id_still_loads(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text(json.dumps({"schema_version": "questions.v2", "run_id": "20260101T000000Z-aaaaaaaa",
                                **_questions()}), encoding="utf-8")
    assert load_artifact("questions", path)["run_id"] == "20260101T000000Z-aaaaaaaa"


def test_a_run_kind_is_stamped_right_after_schema_version(tmp_path):
    path = tmp_path / "ledger.json"
    save_artifact("repo_ledger", _ledger(), path)

    saved = _read(path)
    assert list(saved)[:2] == ["schema_version", "run_id"]
    assert saved["run_id"] == current_run_id()


def test_every_save_in_one_process_shares_the_run_id(tmp_path):
    save_artifact("repo_ledger", _ledger(), tmp_path / "a.json")
    save_artifact("repo_ledger", _ledger(), tmp_path / "b.json")

    assert _read(tmp_path / "a.json")["run_id"] == current_run_id()
    assert _read(tmp_path / "b.json")["run_id"] == current_run_id()


def test_an_explicit_run_id_wins_over_the_process_run(tmp_path):
    path = tmp_path / "ledger.json"
    save_artifact("repo_ledger", _ledger(), path, run_id="explicit")
    assert _read(path)["run_id"] == "explicit"


def test_a_run_id_inside_the_data_is_not_read(tmp_path):
    path = tmp_path / "ledger.json"
    save_artifact("repo_ledger", {**_ledger(), "run_id": "from-source"}, path)
    assert _read(path)["run_id"] == current_run_id()


def test_stamp_sets_the_run_id_of_a_run_kind_and_nothing_for_an_input():
    stamped = stamp("repo_ledger", {"rules": []})
    assert list(stamped)[:2] == ["schema_version", "run_id"]
    assert stamped["run_id"] == current_run_id()
    assert "run_id" not in stamp("dokimi_config", {})


def test_plan_digest_ignores_the_run_id():
    plan = {"cells": [{"id": "a"}], "generated_at": "x"}
    assert plan_digest(plan) == plan_digest({**plan, "run_id": "other", "generated_at": "y"})


def test_a_non_string_run_id_reads_as_none(tmp_path):
    path = tmp_path / "odd.json"
    path.write_text(json.dumps({"schema_version": "repo_ledger.v1", **_ledger(), "run_id": 7}), encoding="utf-8")
    rows = [a.to_json() for a in scan_root(tmp_path).artifacts]
    assert rows[0]["run_id"] is None


def test_a_non_run_kind_gets_no_envelope(tmp_path):
    path = tmp_path / "dokimi.json"
    save_artifact("dokimi_config", {}, path)

    assert "run_id" not in _read(path)


def test_an_invalid_run_id_is_refused_and_nothing_is_written(tmp_path):
    path = tmp_path / "ledger.json"
    with pytest.raises(ArtifactValidationError):
        save_artifact("repo_ledger", _ledger(), path, run_id="")
    assert not path.exists()
    assert path.with_name(path.name + ".rejected").exists()


def test_an_artifact_written_before_the_envelope_still_loads(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"schema_version": "repo_ledger.v1", **_ledger()}), encoding="utf-8")

    loaded = load_artifact("repo_ledger", path)
    assert loaded["rules"] == _ledger()["rules"] and "run_id" not in loaded


@pytest.mark.parametrize("kind", sorted(RUN_KINDS))
def test_every_run_schema_declares_run_id(kind):
    schema = json.loads((SCHEMA_DIR / f"{SCHEMA_VERSIONS[kind]}.json").read_text(encoding="utf-8"))
    assert schema["properties"]["run_id"]["type"] == "string", kind


@pytest.mark.parametrize("name", WIDENED)
def test_the_four_named_closed_schemas_admit_run_id(name):
    schema = json.loads((SCHEMA_DIR / f"{name}.v1.json").read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["properties"]["run_id"]["type"] == "string"
    assert "run_id" not in schema.get("required", [])


def test_scan_reads_run_id_and_reports_none_for_an_old_artifact(tmp_path):
    save_artifact("repo_ledger", _ledger(), tmp_path / "new.json", run_id="20260101T000000Z-aaaaaaaa")
    (tmp_path / "old.json").write_text(
        json.dumps({"schema_version": "repo_ledger.v1", **_ledger()}), encoding="utf-8")

    rows = {row["path"]: row["run_id"] for row in
            (a.to_json() for a in scan_root(tmp_path).artifacts)}

    assert rows == {"new.json": "20260101T000000Z-aaaaaaaa", "old.json": None}
