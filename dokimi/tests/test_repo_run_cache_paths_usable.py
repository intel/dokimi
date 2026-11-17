# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`--cache-root` and `--cache NAME=PATH` of `eval check` and `repo run` are checked before the image build (#826)."""

from __future__ import annotations

import pytest

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


def test_a_cache_of_a_name_the_manifest_does_not_declare_keeps_its_message(setup):
    blocker = _file(setup["tmp"])
    exc = _invoke(setup, "check", "--cache-root", str(setup["tmp"] / "caches"), "--cache", f"nope={blocker}")
    assert exc.code == 1
    assert "--cache names no cache of the manifest: nope" in exc.err
