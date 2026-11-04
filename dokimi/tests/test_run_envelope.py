# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The run envelope (#277, #94 R1): ``save_artifact`` writes ``run_id`` and ``dokimi_version``
after ``schema_version`` on every kind a run produces, and on no input kind."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from dokimi import __version__, artifacts, cli
from dokimi.artifacts import (
    ENVELOPE_KEYS,
    INPUT_KINDS,
    SCHEMA_DIR,
    SCHEMA_VERSIONS,
    ArtifactValidationError,
    load_artifact,
    run_id,
    save_artifact,
    stamp,
    validate_artifact,
)
from dokimi.runner.plan import plan_digest
from dokimi.runs.scan import scan
from dokimi.utils import InputError

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "artifacts"
RUN_KINDS = sorted(set(SCHEMA_VERSIONS) - INPUT_KINDS)


def _schema(kind: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{SCHEMA_VERSIONS[kind]}.json").read_text(encoding="utf-8"))


def _fixture(kind: str) -> dict:
    return json.loads((FIXTURES / f"{SCHEMA_VERSIONS[kind]}.json").read_text(encoding="utf-8"))


@pytest.fixture
def fresh_id(monkeypatch):
    """A process that has not made its run id yet, with no DOKIMI_RUN_ID."""
    monkeypatch.delenv(artifacts.RUN_ID_ENV, raising=False)
    monkeypatch.setattr(artifacts, "_generated_run_id", None)


def test_input_kinds_are_registered_kinds():
    assert INPUT_KINDS < set(SCHEMA_VERSIONS)


@pytest.mark.parametrize("kind", RUN_KINDS)
def test_every_run_kind_schema_declares_the_same_envelope(kind):
    """A new kind is a run kind unless listed in INPUT_KINDS: this fails until its schema declares
    the envelope (or it is listed), so a closed schema cannot reject its first save."""
    properties = _schema(kind)["properties"]
    reference = _schema("answers")["properties"]
    assert {key: properties.get(key) for key in ENVELOPE_KEYS} == \
        {key: reference[key] for key in ENVELOPE_KEYS}
    assert not set(ENVELOPE_KEYS) & set(_schema(kind).get("required", []))  # older files stay valid


def test_the_schema_pattern_is_the_one_run_id_accepts():
    """A loaded file's run_id can name a directory too, as a written one can."""
    assert _schema("answers")["properties"]["run_id"]["pattern"] == f"^{artifacts._RUN_ID.pattern}$"


@pytest.mark.parametrize("kind", sorted(INPUT_KINDS))
def test_no_input_kind_schema_declares_the_envelope(kind):
    assert not set(ENVELOPE_KEYS) & set(_schema(kind).get("properties", {}))


@pytest.mark.parametrize("kind", RUN_KINDS)
def test_a_run_kind_is_saved_with_the_envelope_after_its_schema_version(tmp_path, kind):
    path = tmp_path / "a.json"
    written = save_artifact(kind, _fixture(kind), path)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == written
    assert list(on_disk)[:3] == ["schema_version", "run_id", "dokimi_version"]
    assert (on_disk["run_id"], on_disk["dokimi_version"]) == (run_id(), __version__)


@pytest.mark.parametrize("kind", sorted(INPUT_KINDS))
def test_an_input_kind_is_saved_with_no_envelope(tmp_path, kind):
    written = save_artifact(kind, _fixture(kind), tmp_path / "a.json")
    assert not set(ENVELOPE_KEYS) & set(written)


def test_a_plan_keeps_its_digest_when_saved_in_another_process(tmp_path, monkeypatch):
    """Why a plan is an input: the completeness check compares the plan's digest with the result's
    plan_digest, and a run id in the plan would change that digest in every process."""
    plan = _fixture("plan")
    monkeypatch.setenv(artifacts.RUN_ID_ENV, "first")
    save_artifact("plan", plan, tmp_path / "plan.json")
    monkeypatch.setenv(artifacts.RUN_ID_ENV, "second")
    assert plan_digest(load_artifact("plan", tmp_path / "plan.json")) == plan_digest(plan)


def test_an_envelope_in_the_data_is_replaced_by_this_run(tmp_path):
    """A payload built from a loaded artifact records the process that saved it."""
    stale = {**_fixture("arms"), "run_id": "an-earlier-run", "dokimi_version": "0.0.1"}
    written = save_artifact("arms", stale, tmp_path / "a.json")
    assert (written["run_id"], written["dokimi_version"]) == (run_id(), __version__)
    assert stale["run_id"] == "an-earlier-run"  # the caller's dict is not mutated


def test_an_artifact_saved_before_the_envelope_is_valid_and_lists_with_no_run_id(tmp_path):
    old = {"schema_version": "arms.v1", **_fixture("arms")}
    assert not set(ENVELOPE_KEYS) & set(old)
    (tmp_path / "old.json").write_text(json.dumps(old, indent=2), encoding="utf-8")

    assert load_artifact("arms", tmp_path / "old.json") == old
    (found,) = scan(tmp_path).artifacts
    assert found.run_id is None and found.to_json()["run_id"] is None


@pytest.mark.parametrize("key, value", [("run_id", 7), ("run_id", ""), ("run_id", "../x y"),
                                        ("run_id", "x" * 129), ("dokimi_version", None)])
def test_the_schema_types_the_envelope(key, value):
    with pytest.raises(ArtifactValidationError, match=key):
        validate_artifact("arms", {**_fixture("arms"), key: value})


def test_stamp_sets_what_save_writes(tmp_path):
    """A --json print of what --out saves is the same document."""
    payload = _fixture("task_inspect")
    printed = stamp("task_inspect", dict(payload))
    assert printed == save_artifact("task_inspect", payload, tmp_path / "t.json")
    assert not set(ENVELOPE_KEYS) & set(stamp("plan", _fixture("plan")))


def test_the_run_id_is_made_once_per_process(fresh_id):
    seen: list[str] = []
    threads = [threading.Thread(target=lambda: seen.append(run_id())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(seen)) == 1 and seen[0] == run_id()
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", seen[0])


def test_two_processes_make_different_run_ids(fresh_id, monkeypatch):
    first = run_id()
    monkeypatch.setattr(artifacts, "_generated_run_id", None)
    assert run_id() != first


def test_dokimi_run_id_names_the_run_and_an_empty_one_is_unset(fresh_id, monkeypatch, tmp_path):
    own = run_id()
    monkeypatch.setenv(artifacts.RUN_ID_ENV, "nightly-2026.11.04_1")
    assert save_artifact("arms", _fixture("arms"), tmp_path / "a.json")["run_id"] == "nightly-2026.11.04_1"
    assert save_artifact("eval", _fixture("eval"), tmp_path / "e.json")["run_id"] == "nightly-2026.11.04_1"
    monkeypatch.setenv(artifacts.RUN_ID_ENV, "")
    assert run_id() == own


@pytest.mark.parametrize("value", ["-leading-dash", ".hidden", "a/b", "a b", "../up", "x" * 129, "naïve"])
def test_a_malformed_dokimi_run_id_is_an_input_error(fresh_id, monkeypatch, tmp_path, value):
    monkeypatch.setenv(artifacts.RUN_ID_ENV, value)
    with pytest.raises(InputError, match="DOKIMI_RUN_ID"):
        run_id()
    with pytest.raises(InputError, match="payload kept at"):
        save_artifact("arms", _fixture("arms"), tmp_path / "a.json")
    assert [p.name for p in tmp_path.iterdir()] == ["a.json.rejected"]  # a paid run is not lost
    kept = json.loads((tmp_path / "a.json.rejected").read_text(encoding="utf-8"))
    assert kept == {"schema_version": "arms.v1", **_fixture("arms")}


def test_the_cli_refuses_a_malformed_dokimi_run_id_before_the_command_runs(monkeypatch, capsys):
    ran = []
    monkeypatch.setenv(artifacts.RUN_ID_ENV, "a/b")
    monkeypatch.setattr("sys.argv", ["dokimi", "runs", "list"])
    monkeypatch.setattr("dokimi.commands.runs.cmd_runs_list", lambda args: ran.append(args))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2 and not ran
    assert "DOKIMI_RUN_ID='a/b' is not a run id" in capsys.readouterr().err


def test_runs_show_says_why_a_file_has_no_run_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DOKIMI_RUNS_DIR", str(tmp_path))
    save_artifact("plan", _fixture("plan"), tmp_path / "plan.json")
    (tmp_path / "old.json").write_text(json.dumps({"schema_version": "arms.v1", **_fixture("arms")}),
                                       encoding="utf-8")
    save_artifact("arms", _fixture("arms"), tmp_path / "new.json")
    (tmp_path / "bad.json").write_text(json.dumps({"schema_version": "arms.v1", "run_id": 7, **_fixture("arms")}),
                                       encoding="utf-8")
    shown = {}
    for name in ("plan.json", "old.json", "new.json", "bad.json"):
        monkeypatch.setattr("sys.argv", ["dokimi", "runs", "show", name])
        cli.main()
        shown[name] = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("run_id:"))
    assert shown == {"plan.json": "run_id:     - (an input kind: no envelope)",
                     "old.json": "run_id:     - (saved before the envelope)",
                     "new.json": f"run_id:     {run_id()}",
                     "bad.json": "run_id:     - (not a run id: see the schema errors)"}


def _save_in_a_new_process(path, environ) -> str:
    code = ("import json, sys; from dokimi.artifacts import save_artifact; "
            "print(save_artifact('arms', json.load(sys.stdin), sys.argv[1])['run_id'])")
    done = subprocess.run([sys.executable, "-c", code, str(path)], input=json.dumps(_fixture("arms")),
                          capture_output=True, text=True, env=environ, check=True, timeout=120)
    return done.stdout.strip()


def test_each_process_has_its_own_run_id_and_dokimi_run_id_joins_them(tmp_path):
    environ = {k: v for k, v in os.environ.items() if k != artifacts.RUN_ID_ENV}
    own = {_save_in_a_new_process(tmp_path / f"{n}.json", environ) for n in "ab"}
    joined = {_save_in_a_new_process(tmp_path / f"{n}.json", {**environ, artifacts.RUN_ID_ENV: "ci-42"})
              for n in "cd"}
    assert len(own) == 2 and run_id() not in own
    assert joined == {"ci-42"}
    assert json.loads((tmp_path / "d.json").read_text(encoding="utf-8"))["run_id"] == "ci-42"
