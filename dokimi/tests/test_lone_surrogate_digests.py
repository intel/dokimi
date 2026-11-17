# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A hash of text that holds a lone surrogate (a surrogate-escaped argv value or file name, a ``\\udXXX`` JSON escape) must not raise.

The digest of every input that encodes as UTF-8 stays what it was; two different surrogates stay apart.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="a surrogate-escaped name is a POSIX file-name form")


@posix_only
def test_cache_path_of_a_remote_url_with_a_non_utf8_byte_is_a_directory_name():
    from dokimi.repos.source import RepoSource, cache_path

    source = RepoSource.parse("https://example.com/n\udcff@" + "a" * 40)
    assert source.location == "https://example.com/n\udcff"
    assert cache_path(source.location, Path("/c")) == Path("/c/n_-4d0dae2da255.git")
    assert cache_path("https://example.com/n\udcfe", Path("/c")) == Path("/c/n_-e1417c254653.git")


def test_cache_path_of_a_utf8_url_is_unchanged():
    from dokimi.repos.source import cache_path

    assert cache_path("https://example.com/org/repo.git", Path("/c")) == Path("/c/repo-e9497c38e4fa.git")


@posix_only
def test_harness_id_with_a_non_utf8_byte_and_a_reserved_character_is_a_directory_name():
    from dokimi.harnesses.runner import _safe_id

    assert _safe_id("n\udcff?") == "n\udcff_-2d5da54d"
    assert _safe_id("n\udcfe?") == "n\udcfe_-f6a8699d"


def test_harness_id_with_a_reserved_character_keeps_its_directory_name():
    from dokimi.harnesses.runner import _safe_id

    assert _safe_id("a:b?c") == "a_b_c-f1b58422"
    assert _safe_id("plain") == "plain"


@posix_only
def test_reach_token_of_a_file_name_with_a_non_utf8_byte_is_a_token():
    from dokimi.repos.reach import token

    assert token("s", "n\udcff", "p") == "RCE80092"
    assert token("s", "n\udcfe", "p") == "RC18DC10"


def test_reach_token_of_a_utf8_path_is_unchanged():
    from dokimi.repos.reach import token

    assert token("s", "a/b.py", "p") == "RCC6EA29"


@posix_only
def test_skill_reach_token_of_a_case_id_with_a_non_utf8_byte_is_a_token():
    from dokimi.repos.skill_reach import token

    assert token("s", "n\udcff", "p") == "SBE80092"
    assert token("s", "n\udcfe", "p") == "SB18DC10"


def test_skill_reach_token_of_a_json_escaped_high_surrogate_case_id():
    import json

    from dokimi.repos.skill_reach import token

    assert token("s", json.loads('"n\\ud800"'), "p") == "SB191ACA"  # the value the `\0sp` tag gave before key_bytes
    assert token("s", json.loads('"n\\ud800"'), "p") != token("s", json.loads('"n\\ud801"'), "p")


def test_skill_reach_token_of_a_high_surrogate_differs_from_escaped_bytes_that_encode_alike():
    from dokimi.repos.skill_reach import token

    assert token("s", "\udced\udca0\udc80", "p") != token("s", "\ud800", "p")


def test_skill_reach_token_of_a_utf8_case_id_is_unchanged():
    from dokimi.repos.skill_reach import token

    assert token("s", "case-1", "p") == "SB7667F2"
    assert token("s", "case-1", "description") == "SKE40028"


@posix_only
def test_url_client_cache_key_of_a_source_with_a_non_utf8_byte_is_a_digest():
    from dokimi.mcp.url_client import URLClient

    assert URLClient("n\udcff")._cache_key() == "8a8f844082c0b6790a883eca6bd8260acd6b5ce42eca3e591fe7f9db764e6a57"
    assert URLClient("n\udcfe")._cache_key() == "fad35f9379fa9598c32b2a7fa48575cf213ef99b59955fef2f454bd4a2aff8e8"


def test_url_client_cache_key_of_a_utf8_url_is_unchanged():
    from dokimi.mcp.url_client import URLClient

    key = URLClient("https://example.com/doc")._cache_key()
    assert key == "9813a80c59ae8111adf2b881b481b0a6334db465cd7c961d98cec1830f9aa1db"  # gitleaks:allow: the sha256 of a test URL


def _bundle_digest(path: str, sha256: str = "a") -> str:
    from dokimi.novelty.bundle import Bundle, BundleFile

    files = [BundleFile(path=path, role="reference", text="x", sha256=sha256)]
    return Bundle(kind="skill", name="n", description="d", frontmatter="", text="", files=files).measured_digest()


@posix_only
def test_bundle_measured_digest_of_a_file_name_with_a_non_utf8_byte_is_a_digest():
    assert _bundle_digest("n\udcff") == "sha256:d80bfa98e8ac7a6d87779ff266777366ec351f3af2c3c281a07505b9626f2695"
    assert _bundle_digest("n\udcfe") == "sha256:30c7ad139a4dc659cf4fb1be69484b42bec251091ca030987539b9ea639d0f49"


def test_bundle_measured_digest_of_a_utf8_file_name_is_unchanged():
    assert _bundle_digest("references/a.md", "b" * 8) == (
        "sha256:6c2343b26584af5163cf510a83b2854ecdec51dc82b4ee23ef94f8f8c03181db")


def _graph_candidate(*symbols: str):
    from dokimi.repos.graph_tasks import Candidate

    return Candidate("references", symbols, "run", "run", (), (), (), 1)


@posix_only
def test_graph_task_id_of_a_symbol_with_a_non_utf8_byte_is_an_id():
    from dokimi.repos.graph_tasks import task_id

    assert task_id(_graph_candidate("n\udcff")) == "graph_references_run_8a8f8440"
    assert task_id(_graph_candidate("n\udcfe")) == "graph_references_run_fad35f93"


def test_graph_task_id_of_utf8_symbols_is_unchanged():
    from dokimi.repos.graph_tasks import task_id

    assert task_id(_graph_candidate("scip-python python pkg 1 mod/run().")) == "graph_references_run_89eac09d"
    assert task_id(_graph_candidate("a", "b")) == "graph_references_run_7e18f737"


def _delta_artifact_name(baseline_name: str) -> str:
    from dokimi.eval.matrix_rollup import _paired_plugin_deltas
    from tests.test_matrix_rollup import _arms_artifact

    cells = [(baseline_name, Path("a"), _arms_artifact(baseline_name)),
             ("p", Path("b"), _arms_artifact("p", "caveman:full"))]
    deltas, warnings = _paired_plugin_deltas(cells, Path("/out"))
    assert warnings == [] and len(deltas) == 1
    return Path(deltas[0]["artifact"]).name


def test_matrix_pair_suffix_of_a_cell_name_with_a_lone_surrogate_is_a_digest():
    assert _delta_artifact_name("n\udcff") == "plugin-delta-n-to-p-8598c7f9.json"
    assert _delta_artifact_name("n\udcfe") == "plugin-delta-n-to-p-ce83522b.json"
    assert _delta_artifact_name("n\ud800") == "plugin-delta-n-to-p-7e558003.json"
    assert _delta_artifact_name("n\udbff") == "plugin-delta-n-to-p-e4429709.json"


def test_matrix_pair_suffix_of_a_utf8_cell_name_is_unchanged():
    assert _delta_artifact_name("ok") == "plugin-delta-ok-to-p-c90164a3.json"


# A ``\udXXX`` escape in loaded JSON or YAML is a lone surrogate, a high one (U+D800..U+DBFF) as well as a surrogate-escaped byte.
ESCAPED = json.loads('"n\\udcff"')
HIGH = json.loads('"n\\ud800"')


def test_leakgate_item_digest_of_a_grading_block_with_a_lone_surrogate_is_a_digest():
    from dokimi.eval.leakgate import item_digest

    assert item_digest({"grading": {"expected": ESCAPED}}) == (
        "a18bdbac1467ab19427f1e04fe443c872aa53fb437150e8b705fc0d372ce3ed8")
    assert item_digest({"grading": {"expected": HIGH}}) == (
        "57c5127ebd2735361de101fa626a7c09cd127114536dc54e2fc494b81c128339")


def test_leakgate_item_digest_of_a_utf8_grading_block_is_unchanged():
    from dokimi.eval.leakgate import item_digest

    assert item_digest({"grading": {"expected": "é"}}) == (
        "aa7c1765ac38cd5c4aa3caf862928fb84c612929fc03bf34e8dc263a62e4c6eb")


def test_leakgate_assignment_digest_of_a_content_with_a_lone_surrogate_is_a_digest():
    from dokimi.eval.leakgate import assignment_digest

    assert assignment_digest({"a": "train"}, {"a": ESCAPED}) == (
        "decaed5723af65eaa5a5d7ed0c2d57c229d97bc5a9e27eafa8cb44b06206330e")
    assert assignment_digest({"a": "train"}, {"a": HIGH}) == (
        "e78e6f0126b2fd72c899fcc126d132ecac713bc47210f61279d06c35b136ca4b")
    assert assignment_digest({"a": "train"}, {"a": "x"}) == (
        "64594acc555505b0b1878a481ab8a02e523854c5095a04032aff909c1a6b5f8a")


def test_eval_graph_digest_of_a_node_text_with_a_lone_surrogate_is_a_digest():
    from dokimi.eval_coverage import graph_digest

    def graph(text: str) -> dict:
        return {"nodes": [{"id": "a", "text": text, "path_class": "x"}]}

    assert graph_digest(graph(ESCAPED)) == "bdb633cf9674f5534ea741c409b6d58f8e8274b4401bd12cb696ed7bf1ebb2ab"
    assert graph_digest(graph(HIGH)) == "fe60c99b86c64f7064dd623b34f9782a5cfeb377d8d75a9e0d34cc77d7601337"
    assert graph_digest(graph("é")) == "8f3d895a07b207c28967c2140c969f6586f2c38a649c1cf4aa356db84ffc88ea"


def test_conformance_expectations_digest_of_a_value_with_a_lone_surrogate_is_a_digest():
    from dokimi.repos.conformance import expectations_digest

    assert expectations_digest({"a": ESCAPED}) == "7fe7ff64b4ed8b457fbe3e2b968dbaac3f4fdb70270df7fce0395a4f432ab833"
    assert expectations_digest({"a": HIGH}) == "f7e3caab02e2e96a6cfc0ae5f5ee30e9a607fb12244b108199e78e59b87fcdb5"
    assert expectations_digest({"a": "é"}) == "b3a092a6af48807fa9482b2ee140105575daa26d5b24b3c0e60a7e2dee6683b1"


def test_response_cache_key_of_a_message_with_a_lone_surrogate_is_a_key():
    from dokimi.caching import response_cache_key

    def key(text: str) -> str:
        return response_cache_key(litellm_model="m", messages=[{"role": "user", "content": text}])

    assert key(ESCAPED) == "3b166de6a754a5c0cd83c09b6ec0cb7bd2e30cc41a6c8214c7afa8a6427de7cc"
    assert key(HIGH) == "d7e02dd5562bbfdcdafb1e9d8d81b7ce93d42dea74bee6ff47d1ef077eb37b70"
    assert key("é") == "b096ff5cc3ad41f6b1f773f79e030d90735abf66a34aa8010e40b871422bdc27"


def test_harbor_content_key_of_a_task_toml_with_a_lone_surrogate_is_a_key(tmp_path):
    from dokimi.harnesses.harbor_export import _content_key

    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    assert _content_key(tmp_path, ESCAPED) == "cd926f61766a61fc"
    assert _content_key(tmp_path, HIGH) == "eceb43402bdc9586"
    assert _content_key(tmp_path, "é") == "0a95d7ad70b368da"
    assert _content_key(tmp_path) == "11ebe38479599956"


def test_harbor_content_key_is_reached_by_a_task_yaml_metadata_escape(tmp_path):
    from dokimi.harnesses import load_task
    from dokimi.harnesses.harbor_export import _content_key, _task_toml
    from tests.test_tbench_v1 import _tb1

    task = load_task(_tb1(tmp_path / "tasks", yaml_extra='difficulty: "n\\udcff"\n'))
    assert task.metadata["difficulty"] == ESCAPED
    assert len(_content_key(task.path, _task_toml(task))) == 16


def _topics_cache_dir(tmp_path, monkeypatch, doc_source: str) -> Path:
    """The ``cache_dir`` the pipeline's question step hands the topic extractor for ``doc_source``."""
    import dokimi.questions as questions
    from dokimi.orchestrator.pipeline import EvaluationPipeline
    from dokimi.registry import ProductRegistry

    seen = {}

    class _StopHere(Exception):
        pass

    class _Extractor:
        def __init__(self, **kwargs):
            seen["cache_dir"] = kwargs["cache_dir"]

        def extract_topics(self, **kwargs):
            raise _StopHere

    class _Client:
        requires_declared_library_id = False

    monkeypatch.setattr("dokimi.orchestrator.pipeline.create_doc_source_client", lambda source: _Client())
    monkeypatch.setattr(questions, "RagasSeedExtractor", _Extractor)
    registry_path = tmp_path / "products.yaml"
    registry_path.write_text("products:\n  my_lib:\n    name: My Lib\n    description: d\n    context7_id: org/lib\n",
                             encoding="utf-8")
    pipeline = EvaluationPipeline(product="My Lib", description="d", product_key="my_lib", output_dir=tmp_path / "run",
                                  registry=ProductRegistry(path=registry_path), doc_source=doc_source)
    with pytest.raises(_StopHere):
        pipeline._generate_questions({})
    return seen["cache_dir"]


@posix_only
def test_pipeline_topics_cache_of_a_doc_source_with_a_non_utf8_byte_gets_its_own_directory(tmp_path, monkeypatch):
    first = _topics_cache_dir(tmp_path, monkeypatch, "url:https://example.com/n\udcff")
    second = _topics_cache_dir(tmp_path, monkeypatch, "url:https://example.com/n\udcfe")
    assert first == Path(".cache/topics/173ffc509215f580")
    assert second == Path(".cache/topics/734bd48df45078ff")


def test_pipeline_topics_cache_of_a_utf8_doc_source_is_unchanged(tmp_path, monkeypatch):
    cache_dir = _topics_cache_dir(tmp_path, monkeypatch, "url:https://example.com/doc")
    assert cache_dir == Path(".cache/topics/782971a1dc6a2ae1")


# A POSIX process whose file-system encoding is ASCII (``LC_ALL=C PYTHONUTF8=0``): ``os.fsencode`` cannot encode "é" there.
# Every hash below is of UTF-8 bytes, so its digest must not depend on that encoding.
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def ascii_fs(monkeypatch):
    monkeypatch.setattr(os, "fsencode", lambda name: name.encode("ascii", "surrogateescape"))


def test_cache_path_of_a_non_ascii_url_does_not_depend_on_the_file_system_encoding(ascii_fs):
    from dokimi.repos.source import cache_path

    url = "https://example.com/é"
    assert cache_path(url, Path("/c")) == Path(f"/c/_-{_sha(url)[:12]}.git")


def test_harness_id_with_a_non_ascii_character_does_not_depend_on_the_file_system_encoding(ascii_fs):
    from dokimi.harnesses.runner import _safe_id

    assert _safe_id("é?") == f"é_-{_sha('é?')[:8]}"


def test_reach_tokens_of_a_non_ascii_path_do_not_depend_on_the_file_system_encoding(ascii_fs):
    from dokimi.repos.reach import token
    from dokimi.repos.skill_reach import token as skill_token

    assert token("s", "é", "p") == "RC" + _sha("s\0é\0p")[:6].upper()
    assert skill_token("s", "é", "p") == "SB" + _sha("s\0é\0p")[:6].upper()


def test_url_client_cache_key_of_a_non_ascii_url_does_not_depend_on_the_file_system_encoding(ascii_fs):
    from dokimi.mcp.url_client import URLClient

    assert URLClient("https://example.com/é")._cache_key() == _sha("https://example.com/é")


def test_url_client_cache_key_of_a_non_ascii_url_in_an_ascii_locale_process():
    """The trigger itself: a child process with an ASCII file-system encoding."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from dokimi.mcp.url_client import URLClient; "
            "print(URLClient('https://example.com/\\u00e9')._cache_key())")
    import dokimi

    env = {**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    done = subprocess.run([sys.executable, "-c", code, str(Path(dokimi.__file__).parent.parent)],
                          env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == _sha("https://example.com/é")


def test_bundle_measured_digest_of_a_non_ascii_file_name_does_not_depend_on_the_file_system_encoding(ascii_fs):
    assert _bundle_digest("é") == "sha256:" + _sha("é:a")


def test_graph_task_id_of_a_non_ascii_symbol_does_not_depend_on_the_file_system_encoding(ascii_fs):
    from dokimi.repos.graph_tasks import HASH_LEN, task_id

    assert task_id(_graph_candidate("é")).endswith("_" + _sha("é")[:HASH_LEN])


def test_pipeline_topics_cache_of_a_non_ascii_doc_source_does_not_depend_on_the_file_system_encoding(
        tmp_path, monkeypatch, ascii_fs):
    cache_dir = _topics_cache_dir(tmp_path, monkeypatch, "url:https://example.com/é")
    assert cache_dir == Path(".cache/topics") / _sha("url:https://example.com/é")[:16]


