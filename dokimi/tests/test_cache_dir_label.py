# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`usable_cache_dir` names the setting it was given in each of its messages (#826, #827)."""

from __future__ import annotations

import os

import pytest

from dokimi.commands.cache_dir import CACHE_USE, RETENTION_USE, usable_cache_dir
from dokimi.files import InputError


def _message(raw, **kwargs) -> str:
    with pytest.raises(InputError) as exc:
        usable_cache_dir(raw, **kwargs)
    return str(exc.value)


def test_the_default_label_is_cache_dir_in_a_file_and_a_file_above_it(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert _message(str(blocker)) == f"--cache-dir {blocker} is not a directory; pass a directory {RETENTION_USE}"
    child = blocker / "c"
    assert _message(str(child)) == (f"--cache-dir {child} has {blocker} above it, which is not a directory; "
                                    f"pass a directory {RETENTION_USE}")


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs permission bits that bind the user")
def test_a_custom_label_replaces_the_flag_in_the_not_writable_message(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert _message(str(locked / "new"), label="--cache-root", use=CACHE_USE) == (
            f"--cache-root {locked / 'new'}: {locked} is not writable; pass a directory {CACHE_USE}")
    finally:
        locked.chmod(0o700)


def test_a_custom_label_replaces_the_flag_in_the_not_a_directory_messages(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert _message(str(blocker), use=CACHE_USE, label="--cache build-cache") == (
        f"--cache build-cache {blocker} is not a directory; pass a directory {CACHE_USE}")
    assert "--cache-dir" not in _message(str(blocker / "c"), label="--cache-root")


def test_a_custom_label_does_not_change_what_passes(tmp_path):
    assert usable_cache_dir(None, label="--cache-root") is None
    assert usable_cache_dir("", label="--cache-root") is None
    assert usable_cache_dir(str(tmp_path / "new"), label="--cache-root") == tmp_path / "new"
