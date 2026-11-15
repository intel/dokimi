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
    assert _context7_hit(tmp_path, "org/n\udcff", "q", _sha(b"org/n\xff:q:8000"))["cached"] is True


def test_context7_cache_key_of_a_query_with_a_high_surrogate_is_a_digest(tmp_path):
    assert _context7_hit(tmp_path, "org/x", HIGH, _sha(b"\0sporg/x:n\xed\xa0\x80:8000"))["cached"] is True


def _topics_hit(tmp_path: Path, library_id: str, key: str) -> list[str]:
    from dokimi.questions.ragas_seed import RagasSeedExtractor

    (tmp_path / f"topics_{key}.json").write_text('["parallel_for"]', encoding="utf-8")
    return RagasSeedExtractor(cache_dir=tmp_path).extract_topics(library_id=library_id, library_name="n", max_topics=3)


def test_topics_cache_key_of_a_utf8_library_id_is_unchanged(tmp_path):
    old = hashlib.sha256("uxlfoundation/oneTBB:3:8000".encode()).hexdigest()
    assert _topics_hit(tmp_path, "uxlfoundation/oneTBB", old) == ["parallel_for"]


def test_topics_cache_key_of_a_library_id_with_a_non_utf8_byte_is_a_digest(tmp_path):
    assert _topics_hit(tmp_path, "local:n\udcff", _sha(b"local:n\xff:3:8000")) == ["parallel_for"]


def test_topics_cache_key_of_a_library_id_with_a_high_surrogate_is_a_digest(tmp_path):
    assert _topics_hit(tmp_path, "local:" + HIGH, _sha(b"\0splocal:n\xed\xa0\x80:3:8000")) == ["parallel_for"]


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


def _old_retention_key(statement: bytes, text: bytes) -> str:
    from dokimi.novelty.retention import _CACHE_VERSION

    return _sha(_CACHE_VERSION + statement + b"\0" + text)[:32]


def test_retention_key_of_a_utf8_statement_and_text_is_unchanged():
    old = _old_retention_key("Use café?".encode("utf-8"), "tëxt".encode("utf-8"))
    assert _retention_key(" Use café? ", "tëxt") == old


def test_retention_key_of_a_statement_with_a_lone_surrogate_is_a_digest():
    assert _retention_key(HIGH, "t") == _old_retention_key(b"n\xed\xa0\x80", b"t")
    assert _retention_key(LOW, "t") == _old_retention_key(b"n\xed\xb3\xbf", b"t")


def test_retention_key_of_a_text_with_a_lone_surrogate_is_a_digest():
    assert _retention_key("s", HIGH) == _old_retention_key(b"s", b"n\xed\xa0\x80")
    assert _retention_key("s", LOW) == _old_retention_key(b"s", b"n\xed\xb3\xbf")


def test_question_set_hash_of_a_question_with_a_low_surrogate_is_a_digest():
    assert _set_hash(LOW) == _sha(b"q0:n\xed\xb3\xbf")[:12]
    assert _set_hash(LOW) != _set_hash("n\udcfe")
