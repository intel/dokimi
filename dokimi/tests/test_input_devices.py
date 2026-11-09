# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An input-file flag given a device is ``<flag> <path>: not a file``, exit 2, before any read (#504).

``/dev/zero`` would be read until memory runs out; ``/dev/null`` is the same kind of file and reads as empty
input, so it stands in for it here. https://github.com/intel/dokimi/issues/504
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from dokimi import cli
from dokimi.commands import benchmark
from dokimi.commands.inputs import read_text_or_exit
from dokimi.orchestrator.pipeline import EvaluationPipeline
from dokimi.registry import ProductEntry
from dokimi.utils import InputError, read_json_input, refuse_device

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="/dev/null is a POSIX device")

DEVICE = "/dev/null"
PERSONAS = {"product": "p", "personas": [{"id": "p1", "name": "dev", "skill_level": "beginner"}]}


def _main(monkeypatch, *argv):
    """The exit code of ``dokimi <argv>``."""
    monkeypatch.setattr("sys.argv", ["dokimi", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


def _personas(tmp_path):
    path = tmp_path / "personas.json"
    path.write_text(json.dumps(PERSONAS), encoding="utf-8")
    return str(path)


class _Registry:
    def get(self, key):
        return ProductEntry(key="onetbb", name="oneTBB", description="TBB", doc_sources=["local:docs"])


# --- the shared check -------------------------------------------------------------------------------------


def test_refuse_device_names_the_flag_and_the_path():
    with pytest.raises(InputError) as exc:
        refuse_device(DEVICE, "--file")
    assert str(exc.value) == "--file /dev/null: not a file"
    with pytest.raises(InputError) as exc:
        refuse_device(Path(DEVICE))
    assert str(exc.value) == "/dev/null: not a file"


def test_refuse_device_passes_a_file_a_pipe_a_directory_and_a_missing_path(tmp_path):
    """Only a device or a socket is refused: the read still reports a missing path or a directory."""
    regular = tmp_path / "x.json"
    regular.write_text("{}", encoding="utf-8")
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    passed = [p for p in (regular, fifo, tmp_path, tmp_path / "missing", regular / "under")
              if refuse_device(p, "--x") is None]
    assert passed == [regular, fifo, tmp_path, tmp_path / "missing", regular / "under"]


def test_read_json_input_refuses_a_device_instead_of_reading_it_as_empty_json():
    with pytest.raises(InputError, match=r"^--trigger-set /dev/null: not a file$"):
        read_json_input(DEVICE, flag="--trigger-set")


@pytest.mark.parametrize("suffix", ["/", "/."])
def test_a_device_spelled_with_a_trailing_slash_or_dot_is_still_refused(monkeypatch, capsys, suffix):
    """``os.stat("/dev/null/")`` is ENOTDIR, but ``Path`` drops the suffix and opens the device."""
    device = DEVICE + suffix
    with pytest.raises(InputError, match=rf"^--trigger-set {device}: not a file$"):
        read_json_input(device, flag="--trigger-set")
    with pytest.raises(InputError, match=rf"^--placebo-text {device}: not a file$"):
        read_text_or_exit(device, "--placebo-text")
    assert _main(monkeypatch, "eval", "questions", "generate", "--product", "p", "--personas", device) == 2
    assert capsys.readouterr().err.strip() == f"Error: --personas {device}: not a file"


def test_read_json_input_still_reports_a_missing_file_as_before(tmp_path):
    with pytest.raises(InputError, match="No such file or directory"):
        read_json_input(tmp_path / "missing.json", flag="--trigger-set")


def test_read_text_or_exit_refuses_a_device_instead_of_reading_it_as_empty_text():
    with pytest.raises(InputError, match=r"^--placebo-text /dev/null: not a file$"):
        read_text_or_exit(DEVICE, "--placebo-text")


# --- each command -----------------------------------------------------------------------------------------


def test_personas_validate_refuses_a_device(monkeypatch, capsys):
    assert _main(monkeypatch, "eval", "personas", "validate", "--file", DEVICE) == 2
    assert capsys.readouterr().err.strip() == "Error: --file /dev/null: not a file"


def test_evaluate_refuses_a_device_as_custom_questions(monkeypatch, tmp_path, capsys):
    assert _main(monkeypatch, "evaluate", "--product", "Demo", "--description", "d",
                 "--output-dir", str(tmp_path / "out"), "--custom-questions", DEVICE) == 2
    assert "Error: --custom-questions /dev/null: not a file" in capsys.readouterr().err


def test_the_pipeline_refuses_a_device_as_questions_from(tmp_path):
    with pytest.raises(InputError, match=r"^--questions-from /dev/null: not a file$"):
        EvaluationPipeline(product="Demo", output_dir=tmp_path / "out", description="d", questions_from=DEVICE)


def test_the_custom_set_loader_refuses_a_device_set_after_construction(tmp_path):
    """The Python API can point ``custom_questions_path`` at a device after the constructor's check."""
    pipeline = EvaluationPipeline(product="Demo", output_dir=tmp_path / "out", description="d")
    pipeline.custom_questions_path = Path(DEVICE)
    with pytest.raises(InputError, match=r"^--custom-questions /dev/null: not a file$"):
        pipeline._load_custom_questions()


@pytest.mark.parametrize("command", ["preflight", "run"])
def test_benchmark_refuses_a_device_as_questions_from_with_its_own_message(monkeypatch, tmp_path, capsys, command):
    """Refused before the read, so the message is not a read error's (an empty one, for a MemoryError)."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(benchmark, "_load_registry", lambda args: _Registry())
    monkeypatch.setattr("dokimi.mcp.factory.create_doc_source_client", lambda doc_source: object())
    monkeypatch.setattr(benchmark, "_run_single_library", lambda *a, **k: pytest.fail("the run started"))
    assert _main(monkeypatch, "benchmark", command, "--library", "onetbb", "--doc-source", "local:docs",
                 "--provider", "openai", "--judge-provider", "openai", "--output-dir", str(tmp_path / "out"),
                 "--questions-from", DEVICE) == 2
    err = capsys.readouterr().err
    assert "--questions-from /dev/null: not a file" in err
    assert "questions source invalid" not in err


def test_triggers_generate_refuses_a_device_as_trigger_set(monkeypatch, tmp_path, capsys):
    catalog = tmp_path / "catalog" / "s1"
    catalog.mkdir(parents=True)
    (catalog / "SKILL.md").write_text("---\nname: s1\ndescription: a skill\n---\nbody\n", encoding="utf-8")
    assert _main(monkeypatch, "eval", "triggers", "generate", "--catalog", str(catalog.parent),
                 "--trigger-set", DEVICE, "--format", "questions", "--out", str(tmp_path / "t.json")) == 2
    assert "Error: --trigger-set /dev/null: not a file" in capsys.readouterr().err
    assert not (tmp_path / "t.json").exists()


def test_questions_generate_refuses_a_device_as_personas(monkeypatch, capsys):
    assert _main(monkeypatch, "eval", "questions", "generate", "--product", "p", "--personas", DEVICE) == 2
    assert capsys.readouterr().err.strip() == "Error: --personas /dev/null: not a file"


def test_questions_generate_refuses_a_device_as_topics(monkeypatch, tmp_path, capsys):
    assert _main(monkeypatch, "eval", "questions", "generate", "--product", "p",
                 "--personas", _personas(tmp_path), "--topics", DEVICE) == 2
    assert capsys.readouterr().err.strip() == "Error: --topics /dev/null: not a file"


def test_novelty_analyze_refuses_a_device_as_placebo_text(monkeypatch, capsys):
    assert _main(monkeypatch, "skill", "novelty", "analyze", "--artifact", "skill:data/skills/fastcontext",
                 "--arms", "no_artifact,full,placebo", "--placebo-text", DEVICE) == 2
    assert "Error: --placebo-text /dev/null: not a file" in capsys.readouterr().err


def test_trace_prefilter_refuses_a_device_as_the_guidance_file(monkeypatch, tmp_path, capsys):
    assert _main(monkeypatch, "trace", "prefilter", DEVICE, str(tmp_path / "s.jsonl")) == 2
    assert capsys.readouterr().err.strip() == "Error: guidance /dev/null: not a file"


def test_trace_prefilter_reports_a_device_session_and_still_reads_the_others(monkeypatch, tmp_path, capsys):
    """A device among the sessions is reported like any unreadable session: the rest are read, exit 1."""
    guide = tmp_path / "AGENTS.md"
    guide.write_text("# Rules\n\n- Always run `make ci-local` before a commit.\n", encoding="utf-8")
    good = tmp_path / "good.jsonl"
    good.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n",
                    encoding="utf-8")
    assert _main(monkeypatch, "trace", "prefilter", str(guide), str(good), DEVICE, "--min-sessions", "1") == 1
    out, err = capsys.readouterr()
    assert "✗ /dev/null: not a file" in err
    assert "1 session(s)" in out
