# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`--cache-dir` of `skill shorten` and `skill novelty analyze` is checked before any paid call (#701)."""

from __future__ import annotations

import json
import os

import pytest

from dokimi.cli import build_parser
from dokimi.commands.novelty_run import usable_cache_dir
from dokimi.novelty import cost as novelty_cost
from dokimi.novelty.atoms import Atom, save_atoms
from dokimi.novelty.spans import segment
from dokimi.utils import InputError
from tests.cli_refusal import refusal


def test_usable_cache_dir_accepts_unset_a_directory_and_a_missing_child(tmp_path):
    assert usable_cache_dir(None) is None
    assert usable_cache_dir("") is None
    assert usable_cache_dir(str(tmp_path)) == tmp_path
    assert usable_cache_dir(str(tmp_path / "new" / "deeper")) == tmp_path / "new" / "deeper"


def test_usable_cache_dir_refuses_a_regular_file_naming_the_flag_and_path(tmp_path):
    target = tmp_path / "cache"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(InputError, match="--cache-dir") as err:
        usable_cache_dir(str(target))
    assert str(target) in str(err.value) and "is not a directory" in str(err.value)


def test_usable_cache_dir_refuses_a_child_of_a_regular_file(tmp_path):
    parent = tmp_path / "file"
    parent.write_text("x", encoding="utf-8")
    with pytest.raises(InputError) as err:
        usable_cache_dir(str(parent / "cache"))
    assert str(parent / "cache") in str(err.value) and f"has {parent} above it" in str(err.value)


def test_usable_cache_dir_refuses_a_dangling_symlink_and_a_child_of_one(tmp_path):
    link = tmp_path / "cache"
    try:
        link.symlink_to(tmp_path / "missing-target")
    except OSError:
        pytest.skip("symlinks need privileges here")
    with pytest.raises(InputError, match="is not a directory") as err:
        usable_cache_dir(str(link))
    assert f"--cache-dir {link} is not a directory" in str(err.value)
    with pytest.raises(InputError) as err:
        usable_cache_dir(str(link / "child"))
    assert f"has {link} above it" in str(err.value)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root writes anywhere")
def test_usable_cache_dir_takes_a_read_only_directory_but_refuses_a_child_of_one(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert usable_cache_dir(str(locked)) == locked
        with pytest.raises(InputError) as err:
            usable_cache_dir(str(locked / "cache"))
        assert str(locked / "cache") in str(err.value) and "is not writable" in str(err.value)
    finally:
        locked.chmod(0o700)


def _listing(cache) -> str:
    """The directory's mode and its entries with their modes, for a failure message."""
    entries = ", ".join(f"{p.name}={oct(p.stat().st_mode & 0o777)}" for p in sorted(cache.iterdir()))
    return f"{cache} mode {oct(cache.stat().st_mode & 0o777)} holds [{entries}]"


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root writes anywhere")
def test_a_read_only_cache_dir_replays_what_it_holds_and_decides_a_miss_without_storing(tmp_path):
    from dokimi.novelty.client import LLMClient
    from dokimi.novelty.retention import RetentionOracle

    calls: list[str] = []

    def transport(prompt, system=None):
        calls.append(prompt)
        return json.dumps({"verdict": "retained"}), None

    # Only an oracle with an adjudicator reads or writes the disk cache: a lexical-only one skips it.
    def client():
        return LLMClient(model="m", role="x", transport=transport)

    cache = tmp_path / "cache"
    # Explicit modes: a umask that strips the owner's write bit would otherwise make the first store fail.
    cache.mkdir(mode=0o700)
    cache.chmod(0o700)
    # Both overlaps (0.8 and 0.5) lie in the ambiguous band, so the adjudicator decides them.
    hit = Atom(statement="Thread count equals core count.", question="q", answer="a", span_ids=())
    miss = Atom(statement="Thread count equals core count and queue depth equals worker count.",
                question="q", answer="a", span_ids=())
    text = "Set the thread count to the core count."
    stored = RetentionOracle(client=client(), cache_dir=cache).verdict(hit, text)
    assert stored.method == "llm" and len(calls) == 1
    held = list(cache.iterdir())
    assert len(held) == 1, _listing(cache)
    # The file's mode follows the umask; one that strips the owner's read bit would leave it unreadable.
    held[0].chmod(0o600)
    cache.chmod(0o500)
    try:
        oracle = RetentionOracle(client=client(), cache_dir=usable_cache_dir(str(cache)))
        replayed = oracle.verdict(hit, text)
        assert replayed.method == "cache", f"{_listing(cache)}; _load -> {oracle._load(held[0].stem)}"
        assert replayed.retained == stored.retained
        assert len(calls) == 1
        decided = oracle.verdict(miss, text)
        assert decided.method == "llm" and len(calls) == 2, _listing(cache)
        assert len(list(cache.iterdir())) == 1, _listing(cache)
    finally:
        cache.chmod(0o700)


def test_a_cache_that_cannot_take_a_verdict_decides_it_and_keeps_it_in_memory(tmp_path):
    from dokimi.novelty.retention import RetentionOracle

    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    atom = Atom(statement="Configure module0 with parameter0", question="q", answer="a", span_ids=())
    text = "Configure module0 with parameter0 always."
    oracle = RetentionOracle(cache_dir=blocker / "cache")
    first = oracle.verdict(atom, text)
    assert first.method == "lexical" and first.retained
    assert oracle.verdict(atom, text) is first
    assert blocker.read_text(encoding="utf-8") == "x"


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root writes anywhere")
def test_usable_cache_dir_refuses_a_writable_directory_without_search_permission(tmp_path):
    closed = tmp_path / "closed"
    closed.mkdir()
    closed.chmod(0o600)
    try:
        with pytest.raises(InputError) as err:
            usable_cache_dir(str(closed))
        assert str(closed) in str(err.value) and "is not readable" in str(err.value)
    finally:
        closed.chmod(0o700)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root searches anywhere")
def test_usable_cache_dir_refuses_a_path_below_a_directory_it_cannot_search(tmp_path):
    closed = tmp_path / "closed"
    closed.mkdir(mode=0o700)
    closed.chmod(0o700)
    (closed / "sub").mkdir(mode=0o700)
    (closed / "sub").chmod(0o700)
    closed.chmod(0o600)
    try:
        for raw in (closed / "sub" / "cache", closed / "sub"):
            with pytest.raises(InputError) as err:
                usable_cache_dir(str(raw))
            # Python 3.14 reads the unsearchable ancestor as missing and refuses it as unwritable instead.
            assert f"--cache-dir {raw}: " in str(err.value)
            assert "cannot inspect" in str(err.value) or "is not writable" in str(err.value)
    finally:
        closed.chmod(0o700)


def test_usable_cache_dir_names_the_flag_when_inspecting_the_path_raises(tmp_path, monkeypatch):
    # Python < 3.14 raises PermissionError from is_dir() below a directory without search permission;
    # simulate it so the refusal is checked on every version and as root.
    def deny(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(type(tmp_path), "is_dir", deny)
    raw = tmp_path / "cache"
    with pytest.raises(InputError) as err:
        usable_cache_dir(str(raw))
    message = str(err.value)
    assert f"--cache-dir {raw}: cannot inspect {tmp_path} (Permission denied)" in message
    assert isinstance(err.value.__cause__, PermissionError)


def _atoms(tmp_path):
    rows = [f"Configure module{i} with parameter{i} always." for i in range(5)]
    (tmp_path / "rows.md").write_text("# H\n\n" + "\n\n".join(rows) + "\n", encoding="utf-8")
    spans = segment((tmp_path / "rows.md").read_text(encoding="utf-8"))
    atoms = [Atom(statement=f"Configure module{i} with parameter{i} never restarts service{i}",
                  question="q", answer="a", span_ids=(span.id,))
             for i, span in enumerate(s for s in spans if s.kind != "heading")]
    save_atoms(atoms, spans, tmp_path / "atoms.json", extractor="e", cap=None)


def _never_called(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("a model client was built for an unusable --cache-dir")

    monkeypatch.setattr(novelty_cost, "client", fail)
    monkeypatch.setattr(novelty_cost, "clients", fail)


@pytest.mark.parametrize("capped", [True, False], ids=["max-usd", "no-cap"])
def test_shorten_refuses_a_cache_dir_that_is_a_file_before_any_call(tmp_path, monkeypatch, capped):
    _atoms(tmp_path)
    cache = tmp_path / "cache"
    cache.write_text("x", encoding="utf-8")
    _never_called(monkeypatch)
    args = build_parser().parse_args([
        "skill", "shorten", "--artifact", f"text:{tmp_path / 'rows.md'}",
        "--atoms", str(tmp_path / "atoms.json"), "--model", "o", "--cache-dir", str(cache),
        *(["--max-usd", "0.05"] if capped else [])])
    with refusal() as out:
        args.func(args)
    assert out.code == 2
    assert f"--cache-dir {cache} is not a directory" in out.err
    assert "Traceback" not in out.err


def test_analyze_refuses_a_cache_dir_that_is_a_file_before_any_call(tmp_path, monkeypatch):
    (tmp_path / "rows.md").write_text("# H\n\nConfigure module with parameter always.\n", encoding="utf-8")
    cache = tmp_path / "cache"
    cache.write_text("x", encoding="utf-8")
    _never_called(monkeypatch)
    args = build_parser().parse_args([
        "skill", "novelty", "analyze", "--artifact", f"text:{tmp_path / 'rows.md'}",
        "--model", "o", "--cache-dir", str(cache), "--max-usd", "0.05",
        "--out-dir", str(tmp_path / "out")])
    with refusal() as out:
        args.func(args)
    assert out.code == 2
    assert f"--cache-dir {cache} is not a directory" in out.err
    assert not (tmp_path / "out").exists()


def test_analyze_help_says_the_response_cache_is_opt_in_and_what_a_rerun_costs(capsys):
    # --cache-dir keeps retention verdicts only; the response cache is DOKIMI_RESPONSE_CACHE, opt-in.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["skill", "novelty", "analyze", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "DOKIMI_RESPONSE_CACHE" in text and "opt-in" in text
    assert "pays for every call again" in text
