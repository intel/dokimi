# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`--cache-root` and `--cache NAME=PATH` of `eval check` and `repo run` are checked before the image build (#826)."""

from __future__ import annotations

import errno
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from dokimi.cli import build_parser
from repo_eval_fixture import FakeRunner, fake_harness, make_repo
from tests.cli_refusal import refusal

CLOSING = "pass a directory the build caches can be kept in"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    return {"root": root, "exe": exe, "profile": profile, "runner": runner, "tmp": tmp_path}


def _argv(s, command, *extra):
    out = str(s["tmp"] / "out")
    base = ["--manifest", ".agent-eval/repo-eval.yaml", "--task", "fix_add"]
    if command == "check":
        return ["eval", "check", str(s["root"]), *base, "--out", out, *extra]
    return ["repo", "run", f"{s['root']}@HEAD", *base, "--harness", str(s["profile"]), "--bin", str(s["exe"]),
            "--model", "m-1", "--batch", "b1", "--out", out, "--agent-network", "host", "--arm", "guidance:raw", *extra]


def _invoke(s, command, *extra):
    args = build_parser().parse_args(_argv(s, command, *extra))
    with refusal() as exc:
        args.func(args)
    return exc


def _file(tmp_path, name="cachefile"):
    path = tmp_path / name
    path.write_text("x", encoding="utf-8")
    return path


COMMANDS = ["check", "run"]


@pytest.mark.parametrize("command", COMMANDS)
def test_a_cache_root_that_is_a_regular_file_exits_2_naming_the_flag_before_any_build(command, setup):
    """#826: it was `✗ [Errno 20] Not a directory: '<file>/build-cache'`, exit 1, after the image build."""
    blocker = _file(setup["tmp"])
    exc = _invoke(setup, command, "--cache-root", str(blocker))
    assert exc.code == 2
    assert f"--cache-root {blocker} is not a directory; {CLOSING}" in exc.err
    assert "Errno" not in exc.err and "building" not in exc.err
    assert setup["runner"].builds == [] and blocker.read_text(encoding="utf-8") == "x"


@pytest.mark.parametrize("command", COMMANDS)
def test_a_cache_root_below_a_regular_file_exits_2_and_makes_nothing(command, setup):
    blocker = _file(setup["tmp"])
    root = blocker / "child"
    exc = _invoke(setup, command, "--cache-root", str(root))
    assert exc.code == 2
    assert f"--cache-root {root} has {blocker} above it, which is not a directory; {CLOSING}" in exc.err
    assert setup["runner"].builds == [] and not list((setup["tmp"] / "out").rglob("content"))  # nothing exported


@pytest.mark.parametrize("command", COMMANDS)
def test_a_cache_root_holding_a_regular_file_named_after_a_cache_exits_2(command, setup):
    root = setup["tmp"] / "caches"
    root.mkdir()
    blocker = _file(root, "build-cache")
    exc = _invoke(setup, command, "--cache-root", str(root))
    assert exc.code == 2
    assert f"--cache-root {blocker} is not a directory; {CLOSING}" in exc.err
    assert setup["runner"].builds == []


@pytest.mark.parametrize("command", COMMANDS)
def test_a_cache_override_that_is_a_regular_file_names_the_cache_flag(command, setup):
    blocker = _file(setup["tmp"])
    exc = _invoke(setup, command, "--cache-root", str(setup["tmp"] / "caches"), "--cache", f"build-cache={blocker}")
    assert exc.code == 2
    assert f"--cache build-cache {blocker} is not a directory; {CLOSING}" in exc.err
    assert setup["runner"].builds == [] and not (setup["tmp"] / "caches").exists()  # the root is not made either


@pytest.mark.parametrize("command", COMMANDS)
def test_an_override_replaces_the_root_child_so_a_file_there_is_not_checked(command, setup):
    root = setup["tmp"] / "caches"
    root.mkdir()
    _file(root, "build-cache")
    good = setup["tmp"] / "mine"
    exc = _invoke(setup, command, "--cache-root", str(root), "--cache", f"build-cache={good}")
    assert exc.code == 0 and "--cache-root" not in exc.err
    assert good.is_dir()


@pytest.mark.parametrize("command", COMMANDS)
def test_good_cache_root_and_cache_override_run_as_before_and_make_the_directories(command, setup):
    root = setup["tmp"] / "caches"
    exc = _invoke(setup, command, "--cache-root", str(root))
    assert exc.code == 0
    assert (root / "build-cache").is_dir() and len(setup["runner"].builds) == 1
    mine = setup["tmp"] / "mine" / "deeper"
    exc = _invoke(setup, command, "--cache-root", str(root), "--cache", f"build-cache={mine}")
    assert exc.code == 0 and mine.is_dir()


@pytest.mark.parametrize("command", COMMANDS)
def test_with_no_cache_root_the_default_root_is_used_and_an_override_alone_is_checked(command, setup, monkeypatch):
    home = setup["tmp"] / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DOKIMI_REPO_CACHE", raising=False)
    blocker = _file(setup["tmp"])
    exc = _invoke(setup, command, "--cache", f"build-cache={blocker}")
    assert exc.code == 2 and f"--cache build-cache {blocker} is not a directory; {CLOSING}" in exc.err
    assert not home.exists()  # the refused override did not make the default root
    exc = _invoke(setup, command)
    assert exc.code == 0
    assert list(home.rglob("build-cache")) and "--cache" not in exc.err


@pytest.mark.parametrize("command", COMMANDS)
def test_a_cache_of_a_name_the_manifest_does_not_declare_exits_2_before_any_build(command, setup):
    exc = _invoke(setup, command, "--cache-root", str(setup["tmp"] / "caches"), "--cache", f"nope={setup['tmp'] / 'c'}")
    assert exc.code == 2
    assert "--cache names no cache of the manifest: nope" in exc.err
    assert setup["runner"].builds == [] and not list((setup["tmp"] / "out").rglob("content"))  # nothing exported


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("flag", ["root", "override"])
def test_dotdot_through_a_missing_dir_to_a_file_is_refused_before_any_build(command, flag, setup):
    """The raw path fails `exists()` at `missing`, so the walk up accepted it; `_context` resolves it to the file."""
    blocker = _file(setup["tmp"])
    sneaky = f"{setup['tmp']}/missing/../{blocker.name}"
    extra = ["--cache-root", sneaky] if flag == "root" else ["--cache", f"build-cache={sneaky}"]
    exc = _invoke(setup, command, *extra)
    assert exc.code == 2
    assert "is not a directory" in exc.err and "Errno" not in exc.err
    assert setup["runner"].builds == []


@pytest.mark.parametrize("command", COMMANDS)
def test_dotdot_cache_root_to_a_file_is_refused_even_when_every_cache_is_overridden(command, setup):
    blocker = _file(setup["tmp"])
    sneaky = f"{setup['tmp']}/missing/../{blocker.name}"
    exc = _invoke(setup, command, "--cache-root", sneaky, "--cache", f"build-cache={setup['tmp'] / 'mine'}")
    assert exc.code == 2
    assert f"--cache-root {blocker} is not a directory; {CLOSING}" in exc.err  # the resolved path
    assert setup["runner"].builds == []


@pytest.mark.parametrize("command", COMMANDS)
def test_a_cache_root_that_is_a_file_is_refused_even_when_every_cache_is_overridden(command, setup):
    blocker = _file(setup["tmp"])
    exc = _invoke(setup, command, "--cache-root", str(blocker), "--cache", f"build-cache={setup['tmp'] / 'mine'}")
    assert exc.code == 2 and f"--cache-root {blocker} is not a directory; {CLOSING}" in exc.err
    assert setup["runner"].builds == [] and not (setup["tmp"] / "mine").exists()


@pytest.mark.parametrize("command", COMMANDS)
def test_a_cache_root_that_is_a_file_is_refused_when_the_manifest_declares_no_cache(command, setup):
    """The root is checked whether or not a cache uses it (contract §9)."""
    manifest = setup["root"] / ".agent-eval" / "repo-eval.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["image"]["caches"] = []
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
    subprocess.run(["git", "-C", str(setup["root"]), "commit", "-qam", "no caches"], check=True)
    blocker = _file(setup["tmp"])
    exc = _invoke(setup, command, "--cache-root", str(blocker))
    assert exc.code == 2 and f"--cache-root {blocker} is not a directory; {CLOSING}" in exc.err
    assert setup["runner"].builds == []


def _loops_raise(monkeypatch, error):
    """A symlink to itself fails `Path.resolve()` as it does on Python < 3.13 (3.13 returns the path)."""
    real = Path.resolve

    def resolve(self, *a, **k):
        if self.is_symlink() and os.readlink(self) == str(self):
            raise error
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "resolve", resolve)


def _loop(path):
    os.symlink(str(path), str(path))
    return path


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize(("error", "why"), [(RuntimeError("Symlink loop from 'x'"), "(a symbolic link loop)"),
                                            (OSError(errno.ELOOP, "Too many levels of symbolic links"),
                                             "(Too many levels of symbolic links)")],
                         ids=["runtime-error", "os-error"])
@pytest.mark.parametrize("flag", ["root", "override", "child"])
def test_a_path_that_cannot_be_resolved_exits_2_naming_the_flag_before_any_build(command, error, why, flag, setup, monkeypatch):
    loop = _loop(setup["tmp"] / "loop")
    _loops_raise(monkeypatch, error)
    if flag == "child":
        root = setup["tmp"] / "caches"
        root.mkdir()
        loop, label, extra = _loop(root / "build-cache"), "--cache-root", ["--cache-root", str(root)]
    elif flag == "root":
        label, extra = "--cache-root", ["--cache-root", str(loop)]
    else:
        label, extra = "--cache build-cache", ["--cache", f"build-cache={loop}"]
    exc = _invoke(setup, command, *extra)
    assert exc.code == 2
    assert f"{label} " in exc.err and f"cannot resolve it {why}" in exc.err and CLOSING in exc.err
    assert "Traceback" not in exc.err and "Errno" not in exc.err and setup["runner"].builds == []


@pytest.mark.parametrize("command", COMMANDS)
def test_an_existing_read_only_cache_root_is_accepted(command, setup):
    root = setup["tmp"] / "caches"
    (root / "build-cache").mkdir(parents=True)
    root.chmod(0o555)
    (root / "build-cache").chmod(0o555)
    try:
        exc = _invoke(setup, command, "--cache-root", str(root))
    finally:
        (root / "build-cache").chmod(0o755)
        root.chmod(0o755)
    assert exc.code == 0 and "--cache" not in exc.err


@pytest.mark.parametrize("command", COMMANDS)
def test_with_no_cache_root_a_file_where_the_default_root_goes_exits_2_before_any_build(command, setup, monkeypatch):
    """The default root sits next to $DOKIMI_REPO_CACHE, which itself passes its check."""
    base = setup["tmp"] / "x"
    (base / "repos").mkdir(parents=True)
    blocker = _file(base, "caches")
    monkeypatch.setenv("DOKIMI_REPO_CACHE", str(base / "repos"))
    exc = _invoke(setup, command)
    assert exc.code == 2
    assert f"the build cache root (from $DOKIMI_REPO_CACHE) {blocker / 'toy' / 'build-cache'} has {blocker} above it" in exc.err
    assert CLOSING in exc.err and "Errno" not in exc.err and setup["runner"].builds == []


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("flag", ["root", "override"])
def test_a_bad_cache_path_is_refused_before_a_remote_source_is_cloned(command, flag, setup):
    """No manifest is needed to check them: a missing remote would otherwise fail its clone first (exit 1)."""
    blocker = _file(setup["tmp"])
    extra = ["--cache-root", str(blocker)] if flag == "root" else ["--cache", f"build-cache={blocker}"]
    argv = _argv(setup, command, "--cache-dir", str(setup["tmp"] / "clones"), *extra)
    argv[2] = f"file://{setup['tmp']}/nothere@HEAD"
    args = build_parser().parse_args(argv)
    with refusal() as exc:
        args.func(args)
    label = "--cache-root" if flag == "root" else "--cache build-cache"
    assert exc.code == 2 and f"{label} {blocker} is not a directory; {CLOSING}" in exc.err
    assert not (setup["tmp"] / "clones").exists()
