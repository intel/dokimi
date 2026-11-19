# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#687 Low: state that lies after a rerun or a failed step (https://github.com/intel/dokimi/issues/687)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from dokimi.artifacts import load_artifact
from dokimi.harnesses import container_agent as container_mod
from dokimi.harnesses import load_task
from dokimi.harnesses.registry import create_harness
from dokimi.ingest import docling_loader
from dokimi.ingest.docling_loader import SOURCES_NAME, materialize_markdown
from dokimi.personas.analyzer import PersonaAnalyzer
from dokimi.process import reply
from dokimi.utils import InputError
# _hermetic is autouse: no operator Claude settings, provider env or host `claude` reach a test.
from tests.test_docker_solver_harness import _fixture_task, _hermetic, _pipeline  # noqa: F401

# ── ingest/docling_loader.py: a rerun replaces its own output ─────────────────
# The rules are docs/ingestion-docling.md "Materialize a corpus".


@pytest.fixture
def converted(monkeypatch):
    """Docling replaced by a converter whose output names the call, so a stale file shows."""
    calls: list[Path] = []

    def convert(path):
        calls.append(Path(path))
        return f"# {Path(path).name}, conversion {len(calls)}\n"
    monkeypatch.setattr(docling_loader, "convert_to_markdown", convert)
    return calls


def _docs(tmp_path: Path, *rels: str) -> list[Path]:
    paths = [tmp_path / "raw" / rel for rel in rels]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF")
    return paths


def _md(out: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(out.glob("*.md"))}


def test_a_rerun_replaces_its_own_output_instead_of_writing_a_numbered_copy(tmp_path, converted):
    docs, out = _docs(tmp_path, "a.pdf", "sub/a.pdf"), tmp_path / "md"
    assert materialize_markdown(docs, out) == [out / "a-pdf.md", out / "a-pdf-2.md"]

    assert materialize_markdown(docs, out) == [out / "a-pdf.md", out / "a-pdf-2.md"]
    assert _md(out) == {"a-pdf.md": "# a.pdf, conversion 3\n", "a-pdf-2.md": "# a.pdf, conversion 4\n"}
    record = load_artifact("docling_sources", out / SOURCES_NAME)
    assert record["files"] == [{"name": "a-pdf-2.md", "source": "../raw/sub/a.pdf"},
                               {"name": "a-pdf.md", "source": "../raw/a.pdf"}]


def test_another_source_with_the_name_gets_a_counter_across_runs(tmp_path, converted):
    (first,), (second,) = _docs(tmp_path, "x/a.pdf"), _docs(tmp_path, "y/a.pdf")
    out = tmp_path / "md"
    assert materialize_markdown([first], out) == [out / "a-pdf.md"]
    assert materialize_markdown([second], out) == [out / "a-pdf-2.md"]

    assert materialize_markdown([first], out) == [out / "a-pdf.md"]
    assert materialize_markdown([second], out) == [out / "a-pdf-2.md"]
    assert sorted(_md(out)) == ["a-pdf-2.md", "a-pdf.md"]


def test_the_same_source_by_another_path_is_the_same_source(tmp_path, converted, monkeypatch):
    (doc,), out = _docs(tmp_path, "a.pdf"), tmp_path / "md"
    materialize_markdown([doc], out)
    monkeypatch.chdir(tmp_path)

    assert materialize_markdown([Path("raw/a.pdf")], Path("md")) == [Path("md/a-pdf.md")]
    assert sorted(_md(out)) == ["a-pdf.md"]


def test_a_failed_record_save_leaves_no_file_the_rerun_cannot_place(tmp_path, converted, monkeypatch):
    (doc,), out = _docs(tmp_path, "a.pdf"), tmp_path / "md"

    def full(kind, data, path):
        raise OSError(28, "No space left on device")
    with monkeypatch.context() as patch:
        patch.setattr(docling_loader, "save_artifact", full)
        with pytest.raises(OSError, match="No space"):
            materialize_markdown([doc], out)

    assert materialize_markdown([doc], out) == [out / "a-pdf.md"]
    assert sorted(_md(out)) == ["a-pdf.md"]


def test_a_file_no_record_names_is_kept(tmp_path, converted):
    (doc,), out = _docs(tmp_path, "a.pdf"), tmp_path / "md"
    out.mkdir()
    (out / "a-pdf.md").write_text("hand-written\n", encoding="utf-8")

    assert materialize_markdown([doc], out) == [out / "a-pdf-2.md"]
    assert (out / "a-pdf.md").read_text(encoding="utf-8") == "hand-written\n"


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are not allowed here")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_a_link_at_a_recorded_name_is_not_written_through(tmp_path, converted):
    (doc,), out = _docs(tmp_path, "a.pdf"), tmp_path / "md"
    materialize_markdown([doc], out)
    (out / "a-pdf.md").unlink()
    (tmp_path / "elsewhere.md").write_text("outside\n", encoding="utf-8")
    _link(out / "a-pdf.md", tmp_path / "elsewhere.md")

    assert materialize_markdown([doc], out) == [out / "a-pdf-2.md"]
    assert (tmp_path / "elsewhere.md").read_text(encoding="utf-8") == "outside\n"
    assert (out / "a-pdf.md").is_symlink()
    assert [f["name"] for f in load_artifact("docling_sources", out / SOURCES_NAME)["files"]] == ["a-pdf-2.md"]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_a_link_at_the_record_is_neither_read_nor_written_through(tmp_path, converted, caplog):
    (doc,), out = _docs(tmp_path, "a.pdf"), tmp_path / "md"
    materialize_markdown([doc], out)
    (out / SOURCES_NAME).rename(tmp_path / "kept.json")
    before = (tmp_path / "kept.json").read_text(encoding="utf-8")
    _link(out / SOURCES_NAME, tmp_path / "kept.json")

    with caplog.at_level(logging.WARNING, logger="dokimi.ingest.docling_loader"):
        assert materialize_markdown([doc], out) == [out / "a-pdf-2.md"]
    assert "is a link, which this run replaces" in caplog.text
    assert (tmp_path / "kept.json").read_text(encoding="utf-8") == before
    assert not (out / SOURCES_NAME).is_symlink()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_a_linked_source_is_a_source_of_its_own(tmp_path, converted):
    (doc,), out = _docs(tmp_path, "a.pdf"), tmp_path / "md"
    _link(tmp_path / "raw" / "cur.pdf", doc)
    inputs = docling_loader.discover_documents(tmp_path / "raw")
    assert materialize_markdown(inputs, out) == [out / "a-pdf.md", out / "cur-pdf.md"]

    (doc.parent / "v2.pdf").write_bytes(b"%PDF")
    (tmp_path / "raw" / "cur.pdf").unlink()
    _link(tmp_path / "raw" / "cur.pdf", doc.parent / "v2.pdf")  # repointed: still the source cur.pdf
    assert materialize_markdown(inputs, out) == [out / "a-pdf.md", out / "cur-pdf.md"]
    assert sorted(_md(out)) == ["a-pdf.md", "cur-pdf.md"]


def test_a_source_whose_file_was_deleted_writes_it_again_under_its_name(tmp_path, converted):
    docs, out = _docs(tmp_path, "a.pdf", "sub/a.pdf"), tmp_path / "md"
    materialize_markdown(docs, out)
    (out / "a-pdf.md").unlink()

    assert materialize_markdown(docs[1:], out) == [out / "a-pdf-2.md"]
    assert materialize_markdown(docs[:1], out) == [out / "a-pdf.md"]


def test_a_new_source_does_not_take_the_name_of_a_deleted_file(tmp_path, converted):
    v1, v2 = _docs(tmp_path, "v1/m.pdf", "v2/m.pdf")
    out = tmp_path / "md"
    materialize_markdown([v1, v2], out)
    (out / "m-pdf.md").unlink()
    (v0,) = _docs(tmp_path, "v0/m.pdf")

    assert materialize_markdown([v0, v1, v2], out) == [out / "m-pdf-3.md", out / "m-pdf.md", out / "m-pdf-2.md"]
    assert _md(out)["m-pdf.md"] == "# m.pdf, conversion 4\n"  # v1's text, not v0's


def test_a_name_whose_file_and_source_are_gone_is_free_again(tmp_path, converted):
    (doc,), out = _docs(tmp_path, "a.pdf"), tmp_path / "md"
    materialize_markdown([doc], out)
    (out / "a-pdf.md").unlink()
    (tmp_path / "raw").rename(tmp_path / "corpus")

    assert materialize_markdown([tmp_path / "corpus" / "a.pdf"], out) == [out / "a-pdf.md"]
    assert load_artifact("docling_sources", out / SOURCES_NAME)["files"] == [
        {"name": "a-pdf.md", "source": "../corpus/a.pdf"}]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_a_recorded_source_is_found_when_the_output_directory_is_a_link(tmp_path, converted):
    v1, v0 = _docs(tmp_path / "work", "v1/m.pdf", "v0/m.pdf")
    (tmp_path / "data" / "md").mkdir(parents=True)
    out = tmp_path / "work" / "md"
    _link(out, tmp_path / "data" / "md")
    materialize_markdown([v1], out)
    (out / "m-pdf.md").unlink()

    assert materialize_markdown([v0, v1], out) == [out / "m-pdf-2.md", out / "m-pdf.md"]


@pytest.mark.skipif(os.name == "nt", reason="a backslash is a path separator on Windows")
def test_a_source_name_with_a_backslash_is_converted_and_rerun(tmp_path, converted):
    (doc,), out = _docs(tmp_path, "a\\b.pdf"), tmp_path / "md"
    assert materialize_markdown([doc], out) == [out / "a\\b-pdf.md"]
    assert materialize_markdown([doc], out) == [out / "a\\b-pdf.md"]
    assert sorted(_md(out)) == ["a\\b-pdf.md"]


def test_a_directory_at_a_recorded_name_is_taken(tmp_path, converted):
    (doc,), out = _docs(tmp_path, "a.pdf"), tmp_path / "md"
    materialize_markdown([doc], out)
    (out / "a-pdf.md").unlink()
    (out / "a-pdf.md").mkdir()

    assert materialize_markdown([doc], out) == [out / "a-pdf-2.md"]


def test_a_name_differing_only_in_letter_case_stays_with_the_deleted_files_source(tmp_path, converted):
    upper, lower = _docs(tmp_path, "x/A.pdf", "y/a.pdf")
    out = tmp_path / "md"
    materialize_markdown([upper], out)
    (out / "A-pdf.md").unlink()

    assert materialize_markdown([lower], out) == [out / "a-pdf-2.md"]


def test_a_directory_at_the_record_name_is_refused_naming_the_record(tmp_path, converted, caplog):
    (doc,), out = _docs(tmp_path, "a.pdf"), tmp_path / "md"
    (out / SOURCES_NAME).mkdir(parents=True)

    with caplog.at_level(logging.WARNING, logger="dokimi.ingest.docling_loader"):
        with pytest.raises(InputError, match=f"{SOURCES_NAME} is a directory"):
            materialize_markdown([doc], out)
    assert not list(out.glob("*.md"))
    assert "writes new ones" not in caplog.text


def test_a_source_spelled_in_another_letter_case_is_the_same_source_where_the_platform_folds_case(
        tmp_path, converted, monkeypatch):
    (doc,), out = _docs(tmp_path, "manual.pdf"), tmp_path / "md"
    monkeypatch.setattr(os.path, "normcase", str.lower)  # what Windows does
    materialize_markdown([doc], out)

    assert materialize_markdown([tmp_path / "RAW" / "MANUAL.PDF"], out) == [out / "manual-pdf.md"]
    assert sorted(_md(out)) == ["manual-pdf.md"]


def test_an_unreadable_record_is_named_and_its_files_are_kept(tmp_path, converted, caplog):
    (doc,), out = _docs(tmp_path, "a.pdf"), tmp_path / "md"
    materialize_markdown([doc], out)
    (out / SOURCES_NAME).write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="dokimi.ingest.docling_loader"):
        assert materialize_markdown([doc], out) == [out / "a-pdf-2.md"]
    assert SOURCES_NAME in caplog.text and "are kept" in caplog.text
    assert (out / "a-pdf.md").read_text(encoding="utf-8") == "# a.pdf, conversion 1\n"
    assert load_artifact("docling_sources", out / SOURCES_NAME)["files"] == [
        {"name": "a-pdf-2.md", "source": "../raw/a.pdf"}]


# ── personas/analyzer.py: the use-case section parser ─────────────────────────


def test_a_use_case_bullet_that_names_the_keyword_is_kept():
    readme = "## Use cases\n- Web applications\n- Batch jobs\n\n## Install\n- pip install x\n"
    assert PersonaAnalyzer._extract_use_cases_from_text(readme) == ["web applications", "batch jobs"]


def test_a_bullet_that_names_the_keyword_opens_no_section():
    readme = "## Features\n- Runs any application\n- Fast\n"
    assert PersonaAnalyzer._extract_use_cases_from_text(readme) == []


def test_a_single_hash_heading_closes_the_use_case_section():
    readme = "## Use cases\n- a\n# Install\n- pip install x\n"
    assert PersonaAnalyzer._extract_use_cases_from_text(readme) == ["a"]


def test_a_dot_bullet_is_an_item():
    readme = "## Use cases\n• Robotics\n"
    assert PersonaAnalyzer._extract_use_cases_from_text(readme) == ["robotics"]


def test_a_bold_use_case_line_still_opens_a_section():
    readme = "**Use cases**\n* Robotics\n\n## Install\n- pip install x\n"
    assert PersonaAnalyzer._extract_use_cases_from_text(readme) == ["robotics"]


def test_the_pygithub_path_keeps_a_keyword_bullet_and_reads_the_readme_once():
    client, repo = Mock(), Mock(description="", get_issues=Mock(return_value=[]))
    client.get_repo.return_value = repo
    repo.get_readme.return_value.decoded_content = b"## When to use\n- Desktop applications\n- CLIs\n"
    repo.get_topics.return_value = []

    analysis = PersonaAnalyzer()._analyze_with_pygithub(client, "o/r")
    assert analysis["use_cases"] == ["desktop applications", "clis"]
    assert repo.get_readme.call_count == 1


# ── harnesses/container_agent.py: a sidecar whose removal failed is not stopped ─


def _sidecar_rm(call) -> bool:
    """A removal of the solve's sidecar, not of the verifier's that open() also clears."""
    return call.argv[1] == "rm" and "egress" in call.argv[-1] and not call.argv[-1].endswith("-verify")


def test_a_failed_sidecar_removal_errors_the_cell_and_close_retries_it(tmp_path):
    task = load_task(_fixture_task(tmp_path))
    cli = _pipeline(reward=1.0, answer=lambda call: reply(stderr="Error response from daemon: busy", returncode=1)
                    if _sidecar_rm(call) else None)

    result = create_harness("docker-claude", runner=cli).run(task, output_dir=tmp_path / "out")

    assert (result.returncode, result.reward, result.passed) == (1, None, False)
    assert "docker rm dokimi-egress-" in result.metrics["error"]
    run_log = (tmp_path / "out" / "run.log").read_text(encoding="utf-8")
    assert "[sidecar removal] docker rm dokimi-egress-" in run_log
    assert not [c for c in cli.calls if c.argv[1] == "exec" and "test.sh" in " ".join(c.argv)]
    # open() clears a leftover, stop_proxy() fails, close() tries again and still drops the network
    removals = [i for i, c in enumerate(cli.calls) if _sidecar_rm(c)]
    assert len(removals) == 3
    assert [i for i, c in enumerate(cli.calls) if list(c.argv[1:3]) == ["network", "rm"]][-1] > removals[-1]


def test_a_sidecar_removal_killed_by_a_signal_is_not_stopped():
    sandbox = container_mod.ContainerSandbox(network="n", proxy_name="p", allow=[], binary=None)

    def docker(args, log=None, *, check=True, timeout=None):
        return reply(stderr="killed", returncode=-9) if args[0] == "rm" else reply()

    with pytest.raises(RuntimeError, match="docker rm p failed"):
        sandbox.stop_proxy(docker, None)
    assert not sandbox._stopped


def test_a_verifier_sidecar_whose_removal_failed_is_removed_again_on_close():
    removals: list[str] = []

    def docker(args, log=None, *, check=True, timeout=None):
        if args[0] == "create":
            raise RuntimeError("docker create failed (exit 1); see run.log")
        if args[0] == "rm":
            removals.append(args[-1])
            if args[-1] == "p-verify" and removals.count("p-verify") == 2:  # the removal after the failed start
                return reply(stderr="Error response from daemon: busy", returncode=1)
        return reply()

    sandbox = container_mod.ContainerSandbox(network="n", proxy_name="p", allow=[], binary=None)
    with pytest.raises(RuntimeError, match="docker create failed"):
        sandbox.verifier_sidecar(docker, None, ["pypi.org"])
    sandbox.close(docker, None, None)

    assert removals.count("p-verify") == 3
