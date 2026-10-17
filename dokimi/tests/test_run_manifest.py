# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the reproducibility run manifest."""

from __future__ import annotations

from dokimi import __version__
from dokimi.runner.manifest import build_run_manifest, hash_files, dep_versions


def test_manifest_core_fields():
    m = build_run_manifest()
    assert "generated_at" in m
    assert m["dokimi_version"] == __version__
    assert set(m["git"]) == {"sha", "dirty", "branch"}
    assert m["python"]
    assert "jsonschema" in m["dependencies"]


def test_manifest_includes_spec_and_docs(tmp_path):
    spec = tmp_path / "spec.yaml"
    spec.write_text("version: 1\n")
    d1 = tmp_path / "a.md"
    d1.write_text("alpha")
    d2 = tmp_path / "b.md"
    d2.write_text("beta")
    m = build_run_manifest(spec_path=spec, doc_paths=[d1, d2], models={"answerer": "gpt-4o"})
    assert m["spec"]["sha256"]
    assert m["docs"]["count"] == 2
    assert m["docs"]["sha256"]
    assert m["models"]["answerer"] == "gpt-4o"


def test_hash_files_is_order_independent(tmp_path):
    a = tmp_path / "a.md"
    a.write_text("a")
    b = tmp_path / "b.md"
    b.write_text("b")
    assert hash_files([a, b])["sha256"] == hash_files([b, a])["sha256"]


def test_hash_files_skips_missing(tmp_path):
    a = tmp_path / "a.md"
    a.write_text("a")
    result = hash_files([a, tmp_path / "missing.md"])
    assert result["count"] == 1


def test_dep_versions_unknown_is_none():
    assert dep_versions(["definitely-not-a-real-package-xyz"]) == {
        "definitely-not-a-real-package-xyz": None
    }


def test_git_fields_are_none_when_git_fails_times_out_or_is_missing(monkeypatch):
    """A failed or hung git says nothing about the checkout: no partial output is recorded."""
    import pytest

    from dokimi import process
    from dokimi.runner import manifest

    answers = {"fail": process.reply("abc\n", "fatal: not a git repository", 128),
               "hang": process.reply("abc\n", timed_out=True), "ok": process.reply(" abc\n")}

    def fake_git(*args, **kwargs):
        if args[0] == "missing":
            raise FileNotFoundError("git")
        return answers[args[0]]

    monkeypatch.setattr(process, "git", fake_git)
    assert manifest._git("ok") == "abc"
    for case in ("fail", "hang", "missing"):
        assert manifest._git(case) is None, case
    with pytest.raises(KeyError):  # only a spawn failure is swallowed
        manifest._git("other")
