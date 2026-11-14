# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A failed rename leaves the file that was there, and no tmp file behind.

Each writer below publishes through a tmp sibling and ``os.replace``. The rename is made to
fail: the old file must still be whole and no ``*.tmp`` may remain. A writer that rewrites the
file in place fails the first check, because the new text is already there.
"""

import json
import logging
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from dokimi.atomic_write import write_bytes_atomic, write_text_atomic
from dokimi.caching import RESPONSE_CACHE_ENV, store_response
from dokimi.commands.catalog_probe import _write as write_probe
from dokimi.metrics.usage import UsageRecord
from dokimi.novelty.atoms import save_atoms
from dokimi.repos import graph_tasks
from dokimi.repos.batch_ledger import write_skill_pin
from dokimi.repos.replay import authoring, build
from dokimi.eval.leakgate import lock_split
from dokimi.commands.repo_run import _pin_batch
from dokimi.write_probe import can_write


@pytest.fixture
def failing_rename(monkeypatch):
    def boom(src, dst, *args, **kwargs):
        raise OSError("injected rename failure")
    monkeypatch.setattr(os, "replace", boom)


def _assert_failed_write_keeps(path: Path, old, call, also: dict[Path, str] | None = None) -> None:
    with pytest.raises(OSError, match="injected rename failure"):
        call()
    _assert_kept(path, old, also)


def _assert_kept(path: Path, old, also: dict[Path, str] | None = None) -> None:
    if old is None:
        assert not path.exists()
    else:
        assert path.read_text(encoding="utf-8") == old
    for other, text in (also or {}).items():
        assert other.read_text(encoding="utf-8") == text
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


def test_response_cache_entry_leaves_no_tmp_file(tmp_path, monkeypatch, caplog, failing_rename):
    directory = tmp_path / "cache"
    directory.mkdir()
    monkeypatch.setenv(RESPONSE_CACHE_ENV, str(directory))
    path = directory / "key1.json"
    path.write_text("OLD\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="dokimi.caching"):
        store_response("key1", "text", UsageRecord())
    assert [r.getMessage() for r in caplog.records if r.name == "dokimi.caching"] == [
        "Could not write response cache entry: injected rename failure"]
    _assert_kept(path, "OLD\n")


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
    (dest / "gold.json").write_text("OLD GOLD\n", encoding="utf-8")
    _assert_failed_write_keeps(path, "OLD\n",
                               lambda: graph_tasks.write(plan, SimpleNamespace(out=tmp_path), {}),
                               also={dest / "gold.json": "OLD GOLD\n"})


def test_task_json_from_replay_build_keeps_the_old_file(tmp_path, failing_rename):
    path = tmp_path / "task.json"
    path.write_text("OLD\n", encoding="utf-8")
    (tmp_path / build.GOLD).write_text("OLD GOLD\n", encoding="utf-8")
    (tmp_path / build.PATCH).write_text("OLD PATCH\n", encoding="utf-8")
    _assert_failed_write_keeps(path, "OLD\n",
                               lambda: build._write(tmp_path, {"id": "t1"}, [], b"NEW PATCH\n"),
                               also={tmp_path / build.GOLD: "OLD GOLD\n", tmp_path / build.PATCH: "OLD PATCH\n"})


def test_bytes_are_published_as_they_are(tmp_path):
    path = tmp_path / "change.patch"
    write_bytes_atomic(path, b"a\r\nb\n")
    assert path.read_bytes() == b"a\r\nb\n"


def test_task_json_from_replay_authoring_keeps_the_old_file(tmp_path, failing_rename):
    path = tmp_path / "task.json"
    path.write_text("OLD\n", encoding="utf-8")
    _assert_failed_write_keeps(path, "OLD\n",
                               lambda: authoring._write(tmp_path, {"id": "t1"}, {}))


def test_catalog_probe_writes_a_long_out_name_its_preflight_accepts(tmp_path):
    out = tmp_path / ("a" * 226 + ".json")
    partial = out.with_name(out.name + ".partial")
    assert can_write(tmp_path, child=partial.name)
    write_probe(partial, {"partial": True, "answers": []})
    assert json.loads(partial.read_text(encoding="utf-8")) == {"partial": True, "answers": []}


def test_concurrent_writers_of_one_path_each_publish_a_whole_file(tmp_path):
    path = tmp_path / "k.json"
    texts = [json.dumps({"text": chr(65 + i) * 300_000}) for i in range(4)]
    errors, torn, stop = [], [], threading.Event()

    def write(text):
        for _ in range(15):
            try:
                write_text_atomic(path, text)
            except OSError as exc:
                errors.append(exc)

    def read():
        while not stop.is_set():
            try:
                seen = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            if seen not in texts:
                torn.append(len(seen))

    reader = threading.Thread(target=read)
    reader.start()
    writers = [threading.Thread(target=write, args=(text,)) for text in texts]
    for w in writers:
        w.start()
    for w in writers:
        w.join()
    stop.set()
    reader.join()
    assert not errors and not torn
    assert path.read_text(encoding="utf-8") in texts
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
