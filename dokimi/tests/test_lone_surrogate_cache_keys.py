# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A cache key or id that hashes text with a lone surrogate returns a digest instead of raising ``UnicodeEncodeError``.

A low surrogate (U+DC80..U+DCFF) is a surrogate-escaped byte of a path or argv value, a high one (U+D800..U+DBFF) comes from a
``\\ud800`` JSON escape. The key of every text that encodes as UTF-8 stays what it was: cached results are filed under it.
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

from tests.exists_mock import exists_for_prefix

from dokimi.repos import WorkTree, load_manifest
from dokimi.repos.reach import run_reach
from dokimi.repos.harness_profile import load_profile
from dokimi.utils import key_bytes
from tests.test_repo_reach import _profile, _repo

HIGH = "n\ud800"
LOW = "n\udcff"


def test_key_bytes_keeps_utf8_and_escaped_bytes_and_tags_a_high_surrogate():
    assert key_bytes("café") == "café".encode("utf-8")
    assert key_bytes(LOW) == b"n\xff"
    assert key_bytes(HIGH) == b"\0spn\xed\xa0\x80"
    assert key_bytes("\udced\udca0\udc80") != key_bytes("\ud800")  # the escaped bytes of U+D800 are another text


def test_key_bytes_tags_a_low_surrogate_below_the_escape_range_too():
    assert key_bytes("\udc41") == b"\0sp\xed\xb1\x81"
    assert key_bytes("\ud800\udcff") == b"\0sp\xed\xa0\x80\xed\xb3\xbf"  # the escaped byte is surrogatepass-encoded too


def test_key_bytes_of_a_high_surrogate_and_of_the_text_that_looked_like_its_tag_differ():
    assert key_bytes("\ud800") == b"\0sp\xed\xa0\x80"
    assert key_bytes("\0sp\udced\udca0\udc80") == b"\0e\0sp\xed\xa0\x80"
    assert key_bytes("\0s\udced\udca0\udc80") != key_bytes("\ud800")
    assert key_bytes("\0e") == b"\0e\0e"


class _FixedClock:
    @staticmethod
    def now(tz):
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=tz)


def _default_salt(tmp_path: Path, monkeypatch, label: str) -> str:
    """The salt `run_reach` derives when it is given none, for a tree labelled ``label`` at a fixed time."""
    monkeypatch.setattr("dokimi.repos.reach.datetime", _FixedClock)
    monkeypatch.setenv("FAKE_MODE", "ok")
    tree = WorkTree(_repo(tmp_path))
    tree.label = label
    work = tmp_path / "work"
    work.mkdir()
    payload = run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"),
                        profile=load_profile(_profile(tmp_path)), model="m", out_dir=tmp_path / "out", work_dir=work)
    return payload["salt"]


STAMP = b"2026-01-02T03:04:05+00:00"


def test_reach_default_salt_of_a_utf8_label_is_unchanged(tmp_path, monkeypatch):
    expected = hashlib.sha256(b"/srv/caf\xc3\xa9\0" + STAMP).hexdigest()[:12]
    assert _default_salt(tmp_path, monkeypatch, "/srv/café") == expected


def test_reach_default_salt_of_a_label_with_a_non_utf8_byte_is_a_digest(tmp_path, monkeypatch):
    expected = hashlib.sha256(b"/srv/d\xff\0" + STAMP).hexdigest()[:12]
    assert _default_salt(tmp_path, monkeypatch, "/srv/d\udcff") == expected


def test_reach_default_salt_of_a_label_with_a_high_surrogate_is_a_digest(tmp_path, monkeypatch):
    expected = hashlib.sha256(b"\0sp/srv/d\xed\xa0\x80\0" + STAMP).hexdigest()[:12]
    assert _default_salt(tmp_path, monkeypatch, "/srv/d\ud800") == expected


@pytest.mark.skipif(sys.platform == "win32", reason="a surrogate-escaped name is a POSIX file-name form")
def test_reach_dry_run_over_a_checkout_whose_directory_name_is_not_utf8(tmp_path):
    odd = tmp_path / os.fsdecode(b"d\xff")
    try:
        odd.mkdir()
    except (OSError, UnicodeError):
        pytest.skip("a file system that rejects a name that is not UTF-8")
    tree = WorkTree(_repo(odd))
    (tmp_path / "work").mkdir()
    plan = run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"),
                     profile=load_profile(_profile(tmp_path)), model="m", out_dir=tmp_path / "out",
                     work_dir=tmp_path / "work", dry_run=True)
    assert plan["runs"]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


DOCS = "Use `tbb::parallel_for` and `task_arena`. " * 5  # past the stub limit: a stored answer is replayed


def _context7_hit(tmp_path: Path, library_id: str, query: str, key: str) -> dict:
    """What `get_library_docs` returns when the entry filed under ``key`` is the only one: a hit on that name, or a fetch."""
    from dokimi.mcp.context7 import Context7Client

    (tmp_path / f"{key}.txt").write_text(DOCS, encoding="utf-8")
    return Context7Client(cache_dir=tmp_path).get_library_docs(library_id, query)[0]


def test_context7_cache_key_of_a_utf8_library_and_query_is_unchanged(tmp_path):
    old = hashlib.sha256("uxlfoundation/oneTBB:café?:8000".encode()).hexdigest()  # the key before key_bytes
    assert _context7_hit(tmp_path, "uxlfoundation/oneTBB", "café?", old)["cached"] is True


def test_context7_cache_key_of_a_library_id_with_a_non_utf8_byte_is_a_digest(tmp_path):
    assert _context7_hit(tmp_path, "org/n\udcff", "q", _sha(b"org/n\xed\xb3\xbf:q:8000"))["cached"] is True


def test_context7_cache_key_of_a_query_with_a_high_surrogate_is_a_digest(tmp_path):
    assert _context7_hit(tmp_path, "org/x", HIGH, _sha(b"org/x:n\xed\xa0\x80:8000"))["cached"] is True


def test_context7_cache_key_of_a_nul_led_library_id_is_unchanged(tmp_path):
    assert _context7_hit(tmp_path, "\0org/x", "q", _sha(b"\0org/x:q:8000"))["cached"] is True


def test_context7_cache_keeps_a_library_id_apart_from_the_escaped_bytes_it_encodes_as(tmp_path):
    from dokimi.mcp.context7 import Context7Client

    plain, escaped = _sha("café:q:8000".encode()), _sha(b"caf\xed\xb3\x83\xed\xb2\xa9:q:8000")
    (tmp_path / f"{plain}.txt").write_text("A" + DOCS, encoding="utf-8")
    (tmp_path / f"{escaped}.txt").write_text("B" + DOCS, encoding="utf-8")
    client = Context7Client(cache_dir=tmp_path)
    assert client.get_library_docs("café", "q")[0]["content"].startswith("A")
    assert client.get_library_docs("caf\udcc3\udca9", "q")[0]["content"].startswith("B")


def _topics_hit(tmp_path: Path, library_id: str, key: str) -> list[str]:
    from dokimi.questions.ragas_seed import RagasSeedExtractor

    (tmp_path / f"topics_{key}.json").write_text('["parallel_for"]', encoding="utf-8")
    return RagasSeedExtractor(cache_dir=tmp_path).extract_topics(library_id=library_id, library_name="n", max_topics=3)


def test_topics_cache_key_of_a_utf8_library_id_is_unchanged(tmp_path):
    old = hashlib.sha256("uxlfoundation/oneTBB:3:8000".encode()).hexdigest()
    assert _topics_hit(tmp_path, "uxlfoundation/oneTBB", old) == ["parallel_for"]


def test_topics_cache_key_of_a_library_id_with_a_non_utf8_byte_is_a_digest(tmp_path):
    assert _topics_hit(tmp_path, "local:n\udcff", _sha(b"local:n\xed\xb3\xbf:3:8000")) == ["parallel_for"]


def test_topics_cache_key_of_a_library_id_with_a_high_surrogate_is_a_digest(tmp_path):
    assert _topics_hit(tmp_path, "local:" + HIGH, _sha(b"local:n\xed\xa0\x80:3:8000")) == ["parallel_for"]


def test_topics_cache_key_of_a_nul_led_library_id_is_unchanged(tmp_path):
    assert _topics_hit(tmp_path, "\0org/x", _sha(b"\0org/x:3:8000")) == ["parallel_for"]


def _set_hash(*texts: str) -> str:
    from dokimi.orchestrator.pipeline import compute_question_set_hash

    return compute_question_set_hash([{"id": f"q{n}", "question": text} for n, text in enumerate(texts)])


def test_question_set_hash_of_a_utf8_set_is_unchanged():
    old = hashlib.sha256("q0:café?\nq1:b".encode("utf-8")).hexdigest()[:12]
    assert _set_hash("café?", "b") == old


def test_question_set_hash_of_a_question_with_a_high_surrogate_is_a_digest():
    assert _set_hash(HIGH) == _sha(b"q0:n\xed\xa0\x80")[:12]


def _atom_id(statement: str) -> str:
    from dokimi.novelty.atoms import Atom

    return Atom(statement=statement, question="q", answer="a").id


def test_atom_id_of_a_utf8_statement_is_unchanged():
    assert _atom_id("  Use café?  ") == "a" + hashlib.sha256("Use café?".encode("utf-8")).hexdigest()[:10]


def test_atom_id_of_a_statement_with_a_high_surrogate_is_a_digest():
    assert _atom_id(HIGH) == "a" + _sha(b"n\xed\xa0\x80")[:10]


def test_atom_id_of_a_statement_with_a_low_surrogate_is_a_digest():
    assert _atom_id(LOW) == "a" + _sha(b"n\xed\xb3\xbf")[:10]
    assert _atom_id(LOW) != _atom_id("n\udcfe")


def _retention_key(statement: str, text: str) -> str:
    from dokimi.novelty.atoms import Atom
    from dokimi.novelty.retention import _key

    return _key(Atom(statement=statement, question="q", answer="a", id="a1"), text)


def _v6_retention_key(statement: bytes, text: bytes) -> str:
    from dokimi.novelty.retention import _CACHE_VERSION

    return _sha(_CACHE_VERSION + len(statement).to_bytes(8, "big") + statement + text)[:32]


def test_retention_key_of_a_utf8_statement_and_text_is_the_v6_layout():
    # Not the v5 key on purpose: the layout changed and _CACHE_VERSION moved to v6 (#847).
    expected = _v6_retention_key("Use café?".encode("utf-8"), "tëxt".encode("utf-8"))
    assert _retention_key(" Use café? ", "tëxt") == expected


def test_retention_key_of_a_statement_with_a_lone_surrogate_is_a_digest():
    assert _retention_key(HIGH, "t") == _v6_retention_key(b"n\xed\xa0\x80", b"t")
    assert _retention_key(LOW, "t") == _v6_retention_key(b"n\xed\xb3\xbf", b"t")


def test_retention_key_of_a_text_with_a_lone_surrogate_is_a_digest():
    assert _retention_key("s", HIGH) == _v6_retention_key(b"s", b"n\xed\xa0\x80")
    assert _retention_key("s", LOW) == _v6_retention_key(b"s", b"n\xed\xb3\xbf")


def test_retention_key_of_a_statement_ending_in_nul_is_apart_from_a_text_starting_with_nul():
    assert _retention_key("a\0", "") != _retention_key("a", "\0")


def test_retention_key_of_a_nul_inside_the_text_is_apart_from_one_inside_the_statement():
    assert _retention_key("a", "\0b") != _retention_key("a\0", "b")


def test_retention_key_is_stable_and_strips_the_statement():
    assert _retention_key("  a \n", "t") == _retention_key("a", "t")
    assert _retention_key("a", "t") != _retention_key("a", " t")


def test_url_client_cache_key_of_a_utf8_source_is_unchanged():
    from dokimi.mcp.url_client import URLClient

    assert URLClient("https://example.com/café")._cache_key() == hashlib.sha256("https://example.com/café".encode()).hexdigest()


def test_url_client_cache_key_of_a_source_with_a_non_utf8_byte_is_unchanged():
    from dokimi.mcp.url_client import URLClient

    assert URLClient(LOW)._cache_key() == _sha(b"n\xff")  # the key #716 gave it, kept


def test_url_client_cache_key_of_a_source_with_a_high_surrogate_is_a_digest():
    from dokimi.mcp.url_client import URLClient

    assert URLClient(HIGH)._cache_key() == _sha(b"\0spn\xed\xa0\x80")


def test_url_client_cache_key_of_a_high_surrogate_is_apart_from_the_escaped_bytes_it_encodes_as():
    from dokimi.mcp.url_client import URLClient

    assert URLClient("\ud800")._cache_key() != URLClient("\udced\udca0\udc80")._cache_key()
    assert URLClient("\ud800")._cache_key() != URLClient("\ud801")._cache_key()


class _Stop(Exception):
    pass


def _topics_dir(monkeypatch, doc_source: str) -> Path:
    """The cache directory the question step hands the topics extractor for ``doc_source``."""
    import dokimi.questions as questions
    from dokimi.orchestrator.pipeline import EvaluationPipeline

    seen = {}

    class _Extractor:
        def __init__(self, cache_dir, **kwargs):
            seen["dir"] = cache_dir

        def extract_topics(self, **kwargs):
            raise _Stop

    monkeypatch.setattr("dokimi.orchestrator.pipeline.create_doc_source_client", lambda source: object())
    monkeypatch.setattr(questions, "RagasSeedExtractor", _Extractor)
    pipeline = EvaluationPipeline(product="P", description="d", output_dir=Path("run"), doc_source=doc_source,
                                  context7_id="org/x")
    with pytest.raises(_Stop):
        pipeline._generate_questions({})
    return seen["dir"]


def test_topics_cache_directory_of_a_utf8_doc_source_is_unchanged(monkeypatch):
    old = hashlib.sha256("local:/docs/café".encode()).hexdigest()[:16]
    assert _topics_dir(monkeypatch, "local:/docs/café") == Path(".cache/topics") / old


def test_topics_cache_directory_of_a_doc_source_with_a_non_utf8_byte_is_unchanged(monkeypatch):
    assert _topics_dir(monkeypatch, "local:/d\udcff") == Path(".cache/topics") / _sha(b"local:/d\xff")[:16]


def test_topics_cache_directory_of_a_doc_source_with_a_high_surrogate_is_a_digest(monkeypatch):
    assert _topics_dir(monkeypatch, "local:/d\ud800") == Path(".cache/topics") / _sha(b"\0splocal:/d\xed\xa0\x80")[:16]


@pytest.mark.parametrize("source, raw", [
    ("local:/docs/café", "local:/docs/café".encode()),
    ("local:/d\udcff", b"local:/d\xff"),
    ("local:/d\ud800", b"\0splocal:/d\xed\xa0\x80"),
], ids=["utf8", "non-utf8-byte", "high-surrogate"])
def test_questions_generate_topics_cache_directory_is_keyed_as_the_pipeline_keys_it(tmp_path, monkeypatch, source, raw):
    import argparse

    import dokimi.questions as questions_pkg
    from dokimi.commands import questions as cmd
    from dokimi.mcp import factory

    seen = {}

    class _Extractor:
        def __init__(self, cache_dir, **kwargs):
            seen["dir"] = cache_dir

        def extract_topics(self, **kwargs):
            raise _Stop

    monkeypatch.chdir(tmp_path)
    real_exists = Path.exists  # the doc source paths here are only cache-key input; #1086 refuses a missing one
    monkeypatch.setattr(Path, "exists", exists_for_prefix(real_exists, "/d"))
    monkeypatch.setattr(factory, "create_doc_source_client", lambda source: object())
    monkeypatch.setattr(factory, "resolve_library_id_for_product", lambda *a, **kw: "org/x")
    monkeypatch.setattr(questions_pkg, "RagasSeedExtractor", _Extractor)
    personas = tmp_path / "personas.json"
    personas.write_text('{"personas": [{"id": "dev", "name": "dev", "skill_level": "expert"}]}', encoding="utf-8")
    with pytest.raises(_Stop):
        cmd.cmd_questions_generate(argparse.Namespace(
            product="oneTBB", personas=str(personas), topics=None, doc_source=source, context7_id=None,
            top_k=3, provider="openai", model="m", count=1, validate=False, output=None))
    assert seen["dir"] == Path(".cache/topics") / _sha(raw)[:16]


def test_clone_directory_of_a_url_with_a_high_surrogate_is_a_directory_name():
    from dokimi.repos.source import cache_path

    assert cache_path("https://example.com/n\ud800", Path("/c")) == Path("/c/n_-" + _sha(b"\0sphttps://example.com/n\xed\xa0\x80")[:12] + ".git")


def test_harness_directory_name_of_an_id_with_a_high_surrogate_and_a_reserved_character(tmp_path):
    from dokimi.harnesses.runner import _safe_id

    name = _safe_id("n\ud800?")
    assert name == "n__-" + _sha(b"\0spn\xed\xa0\x80?")[:8]
    (tmp_path / name).mkdir()


def test_harness_directory_name_of_an_id_with_a_high_surrogate_is_a_directory_name(tmp_path):
    from dokimi.harnesses.runner import _safe_id

    name = _safe_id("n\ud800")
    assert name == "n_-" + _sha(b"\0spn\xed\xa0\x80")[:8]
    (tmp_path / name).mkdir()
    assert _safe_id("n\ud801") != name


def test_harness_directory_name_of_an_id_with_a_low_surrogate_above_the_escape_range(tmp_path):
    from dokimi.harnesses.runner import _safe_id

    name = _safe_id("n\udd00?")
    assert name == "n__-" + _sha(b"\0spn\xed\xb4\x80?")[:8]
    (tmp_path / name).mkdir()
    bare = _safe_id("n\udfff")
    assert bare == "n_-" + _sha(b"\0spn\xed\xbf\xbf")[:8]
    (tmp_path / bare).mkdir()
    assert _safe_id("n\udc80") == "n\udc80"  # maps to a byte: unchanged


def test_skill_reach_token_of_a_nul_led_salt_is_the_key_bytes_of_the_salt():
    from dokimi.repos.skill_reach import token

    assert token("\0x", "case", "body") == "SB8C5EEA"  # `main` gave SB04D1CE: key_bytes puts `\0e` before NUL-led text
    assert token("s", "case", "body") != token("\0s", "case", "body")


def test_graph_task_id_of_a_symbol_with_a_high_surrogate_is_an_id():
    from dokimi.repos.graph_tasks import Candidate, task_id

    candidate = Candidate("references", (HIGH,), "run", "run", (), (), (), 1)
    assert task_id(candidate) == "graph_references_run_" + _sha(b"\0spn\xed\xa0\x80")[:8]


def test_question_set_hash_of_a_question_with_a_low_surrogate_is_a_digest():
    assert _set_hash(LOW) == _sha(b"q0:n\xed\xb3\xbf")[:12]
    assert _set_hash(LOW) != _set_hash("n\udcfe")
