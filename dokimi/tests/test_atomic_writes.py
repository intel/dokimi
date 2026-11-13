# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A failed rename leaves the file that was there, and no tmp file behind.

Each writer below publishes through a tmp sibling and ``os.replace``. The rename is made to
fail: the old file must still be whole and no ``*.tmp`` may remain. A writer that rewrites the
file in place fails the first check, because the new text is already there.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dokimi.caching import RESPONSE_CACHE_ENV, store_response
from dokimi.commands.catalog_probe import _write as write_probe
from dokimi.metrics.usage import UsageRecord
from dokimi.novelty.atoms import save_atoms
from dokimi.repos import graph_tasks
from dokimi.repos.batch_ledger import write_skill_pin
from dokimi.repos.replay import authoring, build
from dokimi.eval.leakgate import lock_split
from dokimi.commands.repo_run import _pin_batch


@pytest.fixture
def failing_rename(monkeypatch):
    def boom(src, dst, *args, **kwargs):
        raise OSError("injected rename failure")
    monkeypatch.setattr(os, "replace", boom)


def _assert_failed_write_keeps(path: Path, old, call) -> None:
    try:
        call()
    except OSError:
        pass
    if old is None:
        assert not path.exists()
    else:
        assert path.read_text(encoding="utf-8") == old
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


def test_skill_pin_keeps_the_old_file(tmp_path, failing_rename):
    batch = tmp_path / "batch"
    (batch / "_eval").mkdir(parents=True)
    path = batch / "_eval" / "skills.json"
    path.write_text("OLD\n", encoding="utf-8")
    _assert_failed_write_keeps(path, "OLD\n", lambda: write_skill_pin(batch, {"arm": [["s", "x"]]}))


def test_repo_run_batch_pin_keeps_the_old_file(tmp_path, failing_rename):
    batch = tmp_path / "batch"
    (batch / "_eval").mkdir(parents=True)
    path = batch / "_eval" / "batch.json"
    _assert_failed_write_keeps(path, None, lambda: _pin_batch(batch, {"model": "m"}))


def test_leakgate_lock_keeps_no_partial_lock(tmp_path, failing_rename):
    path = tmp_path / "lock.json"
    _assert_failed_write_keeps(
        path, None,
        lambda: lock_split({"t1": "train"}, {"t1": "body"}, path, "2026-11-13T00:00:00Z"),
    )


def test_atoms_file_keeps_the_old_file(tmp_path, failing_rename):
    path = tmp_path / "atoms.json"
    path.write_text("OLD\n", encoding="utf-8")
    _assert_failed_write_keeps(path, "OLD\n", lambda: save_atoms([], [], path))


def test_response_cache_entry_leaves_no_tmp_file(tmp_path, monkeypatch, failing_rename):
    directory = tmp_path / "cache"
    directory.mkdir()
    monkeypatch.setenv(RESPONSE_CACHE_ENV, str(directory))
    path = directory / "key1.json"
    path.write_text("OLD\n", encoding="utf-8")
    _assert_failed_write_keeps(path, "OLD\n", lambda: store_response("key1", "text", UsageRecord()))


def test_catalog_probe_report_keeps_the_old_file(tmp_path, failing_rename):
    path = tmp_path / "probe.json"
    path.write_text("OLD\n", encoding="utf-8")
    _assert_failed_write_keeps(path, "OLD\n", lambda: write_probe(path, {"a": 1}))


def test_task_json_from_graph_tasks_keeps_the_old_file(tmp_path, monkeypatch, failing_rename):
    monkeypatch.setattr(graph_tasks, "_task", lambda c, g, opts, idx: ({"id": "t1"}, []))
    plan = SimpleNamespace(granularity="files",
                           outcomes=[SimpleNamespace(status="written", candidate=None, task="t1")])
    dest = tmp_path / "t1"
    dest.mkdir()
    path = dest / "task.json"
    path.write_text("OLD\n", encoding="utf-8")
    _assert_failed_write_keeps(path, "OLD\n",
                               lambda: graph_tasks.write(plan, SimpleNamespace(out=tmp_path), {}))


def test_task_json_from_replay_build_keeps_the_old_file(tmp_path, failing_rename):
    path = tmp_path / "task.json"
    path.write_text("OLD\n", encoding="utf-8")
    _assert_failed_write_keeps(path, "OLD\n",
                               lambda: build._write(tmp_path, {"id": "t1"}, [], b""))


def test_task_json_from_replay_authoring_keeps_the_old_file(tmp_path, failing_rename):
    path = tmp_path / "task.json"
    path.write_text("OLD\n", encoding="utf-8")
    _assert_failed_write_keeps(path, "OLD\n",
                               lambda: authoring._write(tmp_path, {"id": "t1"}, {}))
