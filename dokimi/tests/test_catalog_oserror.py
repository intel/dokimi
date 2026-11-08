# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An OS error on one skill's files is that skill's error row, not a crash of the catalog scan.

The sibling is simulated: its ``SKILL.md`` is read, or its path stat-ed, through a monkeypatched
``Path`` method that raises ``ENAMETOOLONG``, the error a name over the filesystem limit gives.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.external import paths as path_checks
from dokimi.external import read_skill_manifest, review_skills


def _skill(root: Path, name: str, description: str = "Use for things.") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# Body\n", encoding="utf-8")


def _too_long(path: Path) -> OSError:
    return OSError(errno.ENAMETOOLONG, os.strerror(errno.ENAMETOOLONG), str(path))


def test_load_error_on_a_sibling_is_an_error_row(tmp_path, monkeypatch):
    """Pins the load path, which already caught OSError before this change; the other tests pin the fix."""
    _skill(tmp_path, "good")
    _skill(tmp_path, "sib")
    real_read_text = Path.read_text

    def read_text(self, *args, **kwargs):
        if self.parent.name == "sib":
            raise _too_long(self)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    entries = {e.name: e for e in read_skill_manifest(tmp_path)}

    assert entries["good"].loaded
    assert not entries["sib"].loaded
    assert "File name too long" in entries["sib"].error


@pytest.mark.parametrize("method", ["read_text", "read_bytes"])
def test_read_error_on_a_loaded_skill_is_an_error_review(tmp_path, monkeypatch, method):
    _skill(tmp_path, "good")
    _skill(tmp_path, "sib")
    entries = read_skill_manifest(tmp_path)  # both load; the failure comes after
    real = getattr(Path, method)

    def fail_for_sib(self, *args, **kwargs):
        if self.parent.name == "sib":
            raise _too_long(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, fail_for_sib)
    reviews = dict(zip((e.name for e in entries), review_skills(entries), strict=True))

    assert reviews["good"].state == "ok"
    assert reviews["sib"].state == "error"
    assert [f.code for f in reviews["sib"].findings] == ["unreadable"]


@pytest.mark.parametrize("error", [
    OSError(errno.ENAMETOOLONG, "File name too long"),
    UnicodeDecodeError("utf-8", b"", 0, 1, "bad"),
    RuntimeError("loop"),
    ValueError("NUL"),
], ids=["OSError", "UnicodeDecodeError", "RuntimeError", "ValueError"])
def test_every_read_failure_kind_on_a_loaded_skill_is_an_error_review(tmp_path, monkeypatch, error):
    _skill(tmp_path, "good")
    _skill(tmp_path, "sib")
    entries = read_skill_manifest(tmp_path)
    real_read_text = Path.read_text

    def fail_for_sib(self, *args, **kwargs):
        if self.parent.name == "sib":
            raise error
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_for_sib)
    reviews = dict(zip((e.name for e in entries), review_skills(entries), strict=True))

    assert reviews["good"].state == "ok"
    assert reviews["sib"].state == "error"
    assert [f.code for f in reviews["sib"].findings] == ["unreadable"]


@pytest.mark.parametrize("error", [OSError(errno.ELOOP, "Too many levels of symbolic links"), RuntimeError("loop")],
                         ids=["OSError", "RuntimeError"])
def test_resolve_error_on_a_loaded_skill_is_an_error_review(tmp_path, monkeypatch, error):
    # The root check resolves the skill's directory: a loaded skill whose path cannot be resolved is its error row too.
    _skill(tmp_path, "good")
    _skill(tmp_path, "sib")
    entries = read_skill_manifest(tmp_path)
    real_resolve = Path.resolve

    def refuse_for_sib(self, *args, **kwargs):
        if self.name == "sib":
            raise error
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", refuse_for_sib)
    reviews = dict(zip((e.name for e in entries), review_skills(entries), strict=True))

    assert reviews["good"].state == "ok"
    assert reviews["sib"].state == "error"
    assert [f.code for f in reviews["sib"].findings] == ["unreadable"]


@pytest.mark.parametrize("method", ["is_dir", "is_file"])
def test_stat_error_on_an_unloaded_sibling_is_no_review_and_no_crash(tmp_path, monkeypatch, method):
    _skill(tmp_path, "good")
    _skill(tmp_path, "bad", description="")  # no description: does not load
    real = getattr(Path, method)

    def fail_for_bad(self):
        if "bad" in (self.name, self.parent.name):
            raise _too_long(self)
        return real(self)

    monkeypatch.setattr(Path, method, fail_for_bad)
    entries = read_skill_manifest(tmp_path)
    reviews = dict(zip((e.name for e in entries), review_skills(entries), strict=True))

    assert reviews["bad"] is None
    assert reviews["good"].state == "ok"


@pytest.mark.parametrize("extra", [[], ["--per-skill"]], ids=["whole-catalog", "per-skill"])
def test_unreadable_skill_fails_the_scan_like_a_load_error(tmp_path, monkeypatch, extra):
    # A load error always exits 1; a SKILL.md read failure after load must not exit 0 without --strict,
    # on the whole-catalog scan and on --per-skill (whose row has no error, only the unreadable finding).
    _skill(tmp_path, "good")
    _skill(tmp_path, "sib")
    real_read_bytes = Path.read_bytes

    def read_bytes(self):
        if self.parent.name == "sib":
            raise _too_long(self)
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", *extra])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 1


@pytest.mark.parametrize("error", [OSError, RuntimeError, ValueError])
def test_a_name_the_os_refuses_is_an_answer_not_a_crash(tmp_path, monkeypatch, error):
    """The path helpers answer for a name the OS refuses to resolve (a symlink loop, a NUL byte) or stat."""
    def refuse_resolve(self, *args, **kwargs):
        raise error("name refused by the OS")

    def refuse_exists(self, *args, **kwargs):
        raise OSError(errno.ENAMETOOLONG, os.strerror(errno.ENAMETOOLONG), str(self))

    monkeypatch.setattr(Path, "resolve", refuse_resolve)
    monkeypatch.setattr(Path, "exists", refuse_exists)
    target = tmp_path / "x"
    assert path_checks._resolved(target) is None
    assert path_checks._exists(target) is False
    assert path_checks._in_checkout(target, tmp_path) is None
