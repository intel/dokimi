# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for example execution policy and sandbox boundaries."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from dokimi.metrics.example_runner import (
    ExecutionPolicy,
    ExecutionRefused,
    extract_examples,
    run_example,
    score_examples,
)
from dokimi import process
from dokimi.process import FakeRunner, Limits, reply


# Trusted snippets opt out only on macOS; default-cap refusal has dedicated tests.
HOST_MEMORY_MB = None if sys.platform == "darwin" else 512
_NOT_WINDOWS = pytest.mark.skipif(sys.platform == "win32",
                                  reason="host subprocess examples are refused on Windows")

def test_extract_examples_with_metadata():
    md = "text\n```python title=\"x.py\"\nprint(1)\n```\nmore\n```bash\nls\n```\n"
    examples = extract_examples(md)
    assert examples == [("python", "print(1)"), ("bash", "ls")]


def test_extract_examples_tab_indented_fence_is_dedented():
    doc = "-\tstep\n\n\t```python\n\tx = 1\n\ty = 2\n\t```\n"
    [(lang, code)] = extract_examples(doc)
    assert (lang, code) == ("python", "x = 1\ny = 2")
    compile(code, "<example>", "exec")


def test_extract_examples_body_line_indented_less_than_fence_keeps_its_indent():
    doc = "    ```python\n  x = 1\n    y = 2\n    ```\n"
    assert extract_examples(doc) == [("python", "x = 1\ny = 2")]


def test_extract_examples_tilde_fence():
    md = "~~~python\nprint(1)\n~~~\n"
    assert extract_examples(md) == [("python", "print(1)")]


def test_extract_examples_longer_fence_keeps_inner_fence_as_content():
    md = "````markdown\n```python\nprint(1)\n```\n````\n"
    assert extract_examples(md) == [("markdown", "```python\nprint(1)\n```")]


def test_extract_examples_untagged_block_is_not_paired_across_blocks():
    md = "```\nplain\n```\ntext\n```python\nprint(1)\n```\n"
    assert extract_examples(md) == [("python", "print(1)")]


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x0c", "\x0b", "\x85"])
def test_extract_examples_only_splits_on_real_line_ends(sep):
    doc = f"```python\ns = 'a{sep}b'\nprint(1)\n```\n"
    assert extract_examples(doc) == [("python", f"s = 'a{sep}b'\nprint(1)")]


def test_extract_examples_crlf_and_cr_documents_still_parse():
    assert extract_examples("```python\r\nx = 1\r\n```\r\n") == [("python", "x = 1")]
    assert extract_examples("```python\rx = 1\r```\r") == [("python", "x = 1")]


def test_extract_examples_unclosed_tagged_fence_runs_to_the_end():
    assert extract_examples("```python\nprint(1)\n") == [("python", "print(1)")]
    assert extract_examples("```python\nprint(1)```") == [("python", "print(1)```")]


def test_extract_examples_unclosed_untagged_fence_is_not_an_example():
    assert extract_examples("```\nprint(1)\n") == []


def test_default_backend_does_not_execute(tmp_path, monkeypatch):
    """Default policy is 'none': nothing runs, examples are skipped."""
    monkeypatch.delenv("DOC_BENCH_EXAMPLE_BACKEND", raising=False)
    runner = FakeRunner()

    res = run_example("python", "print('hi')", ExecutionPolicy(backend="none"), runner)
    assert res.status == "skipped"
    assert res.passed is None
    assert runner.calls == []


def test_subprocess_refuses_without_allow_host():
    res = run_example("python", "print('x')", ExecutionPolicy(backend="subprocess", allow_host=False))
    assert res.status == "failed"
    assert "allow_host" in (res.error or "")


def test_language_allow_list():
    res = run_example("ruby", "puts 1", ExecutionPolicy(backend="subprocess", allow_host=True))
    assert res.status == "skipped"
    assert "disallowed" in (res.error or "") or "Unsupported" in (res.error or "")


@_NOT_WINDOWS
def test_subprocess_executes_when_opted_in(tmp_path):
    res = run_example(
        "python", "print('ok')", ExecutionPolicy(backend="subprocess", allow_host=True, mem_mb=HOST_MEMORY_MB, timeout=10)
    )
    assert res.status == "passed"
    assert res.passed is True


@_NOT_WINDOWS
@pytest.mark.parametrize("linked_tmp", [False, True])
def test_subprocess_gets_its_own_home_and_no_parent_secrets(monkeypatch, tmp_path, linked_tmp):
    # The temp dir may sit behind a symlink (macOS /var -> /private/var), and
    # getcwd() reports the resolved path, so the two are compared resolved.
    if linked_tmp:
        (tmp_path / "real").mkdir()
        try:
            (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
        except OSError:
            pytest.skip("this OS refuses an unprivileged symlink")
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "link"))
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    code = (
        "import os, sys; "
        "home = os.path.realpath(os.environ.get('HOME', '')); "
        "sys.exit(0 if home == os.path.realpath(os.getcwd()) and 'OPENAI_API_KEY' not in os.environ else 1)"
    )
    res = run_example("python", code, ExecutionPolicy(backend="subprocess", allow_host=True, mem_mb=HOST_MEMORY_MB, timeout=10))
    assert res.status == "passed", res.error


@_NOT_WINDOWS
def test_subprocess_reports_failure(tmp_path):
    res = run_example(
        "python", "import sys; sys.exit(3)",
        ExecutionPolicy(backend="subprocess", allow_host=True, mem_mb=HOST_MEMORY_MB, timeout=10),
    )
    assert res.status == "failed"
    assert "Exit code 3" in (res.error or "")


@_NOT_WINDOWS
def test_subprocess_timeout_enforced():
    res = run_example(
        "python", "import time; time.sleep(30)",
        ExecutionPolicy(backend="subprocess", allow_host=True, mem_mb=HOST_MEMORY_MB, timeout=1),
    )
    assert res.status == "failed"
    assert "Timeout" in (res.error or "")


@_NOT_WINDOWS
def test_subprocess_stderr_flood_is_bounded_in_the_parent():
    """A snippet that floods stderr until its timeout: the parent keeps a bounded head and
    tail, not everything it wrote (it used to grow to gigabytes in a few seconds)."""
    seen: list[process.Completed] = []

    def recording(*args, **kwargs):
        seen.append(process.run(*args, **kwargs))
        return seen[-1]

    code = "import sys\nwhile True:\n    sys.stderr.write('x' * 65536)\n"
    res = run_example("python", code, ExecutionPolicy(backend="subprocess", allow_host=True, mem_mb=HOST_MEMORY_MB, timeout=2),
                      recording)
    assert res.status == "failed" and res.error == "Timeout after 2s"
    (done,) = seen
    assert done.truncated and len(done.stderr_bytes) < process.CAPTURE_LIMIT + 200


def test_score_examples_skipped_scores_one(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("```python\nprint(1)\n```\n")
    rate, results = score_examples(doc, ExecutionPolicy(backend="none"))
    assert rate == 1.0
    assert results[0].status == "skipped"


def test_score_examples_no_examples(tmp_path):
    doc = tmp_path / "d.md"
    doc.write_text("no code here")
    rate, results = score_examples(doc, ExecutionPolicy(backend="none"))
    assert rate == 1.0
    assert results == []


def test_from_config_env_override(monkeypatch):
    monkeypatch.setenv("DOC_BENCH_EXAMPLE_BACKEND", "none")
    policy = ExecutionPolicy.from_config({"execution": {"backend": "subprocess", "allow_host": True}})
    assert policy.backend == "none"


def test_from_config_nested_and_legacy():
    nested = ExecutionPolicy.from_config({"execution": {"backend": "docker", "timeout": 9}})
    assert nested.backend == "docker" and nested.timeout == 9
    legacy = ExecutionPolicy.from_config({"timeout": 7})
    assert legacy.backend == "none" and legacy.timeout == 7


def test_docker_backend_missing_docker():
    """Docker backend surfaces a clear error if docker is absent."""
    def _no_docker(call):
        raise FileNotFoundError(call.argv[0])

    res = run_example("python", "print(1)", ExecutionPolicy(backend="docker"), FakeRunner(_no_docker))
    assert res.status == "failed"
    assert "docker is not installed" in (res.error or "")


def test_docker_backend_locks_the_container_down():
    runner = FakeRunner(lambda call: reply(stderr="Traceback: boom", returncode=1))
    res = run_example("python", "print(1)", ExecutionPolicy(backend="docker", timeout=7), runner)
    assert res.status == "failed" and "Exit code 1: Traceback: boom" in (res.error or "")
    (call,) = runner.calls
    argv = call.argv
    assert argv[:3] == ("docker", "run", "--rm")
    assert argv[argv.index("--network") + 1] == "none" and "--read-only" in argv
    assert argv[argv.index("timeout") + 1] == "7" and call.timeout == 37


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits: Windows chmod sets only read-only")
def test_docker_backend_mount_is_readable_by_a_root_without_capabilities():
    # The container's root has no CAP_DAC_OVERRIDE, and the mount belongs to the host user:
    # it can enter the directory and read the snippet only through the "other" permission bits.
    seen = {}

    def _modes(call):
        work = Path(next(a for a in call.argv if a.endswith(":/work:ro")).removesuffix(":/work:ro"))
        seen["dir"] = work.stat().st_mode & 0o777
        seen["script"] = (work / "snippet.py").stat().st_mode & 0o777
        return reply()

    old = os.umask(0o077)
    try:
        res = run_example("python", "print(1)", ExecutionPolicy(backend="docker"), FakeRunner(_modes))
    finally:
        os.umask(old)
    assert res.status == "passed"
    assert seen == {"dir": 0o755, "script": 0o644}


def test_docker_backend_reports_a_timeout():
    """A docker run killed at the deadline is a timeout, not exit 124 from the snippet."""
    runner = FakeRunner(lambda call: reply(timed_out=True))
    res = run_example("python", "print(1)", ExecutionPolicy(backend="docker", timeout=7), runner)
    assert res.status == "failed" and res.error == "Timeout after 7s"


def test_subprocess_backend_asks_for_limits_and_a_scrubbed_env(monkeypatch):
    monkeypatch.setattr(process.sys, "platform", "linux")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    runner = FakeRunner()
    res = run_example("python", "print(1)",
                      ExecutionPolicy(backend="subprocess", allow_host=True, timeout=3, mem_mb=64), runner)
    assert res.status == "passed"
    (call,) = runner.calls
    assert call.limits == Limits(cpu_sec=3, memory_bytes=64 * 1024 * 1024)
    assert "OPENAI_API_KEY" not in call.env and call.env["HOME"] == str(call.cwd)
    assert call.capture_stdout is False


def test_error_status_counts_as_failure(tmp_path):
    """An unknown backend yields status=error and must not score 1.0."""
    doc = tmp_path / "d.md"
    doc.write_text("```python\nprint(1)\n```\n")
    rate, results = score_examples(doc, ExecutionPolicy(backend="bogus"))
    assert results[0].status == "error"
    assert rate == 0.0


def test_extract_examples_indented_fence_in_a_list_item_is_found():
    md = "1. step\n\n    ```bash\n    ls\n    ```\n"
    assert extract_examples(md) == [("bash", "ls")]


def test_extract_examples_closing_fence_must_use_the_same_character():
    md = "```python\nprint(1)\n~~~\nprint(2)\n```\n"
    assert extract_examples(md) == [("python", "print(1)\n~~~\nprint(2)")]


def test_extract_examples_text_after_a_closing_fence_keeps_the_block_open():
    md = "```python\nprint(1)\n``` trailing\nprint(2)\n```\n"
    assert extract_examples(md) == [("python", "print(1)\n``` trailing\nprint(2)")]


def test_extract_examples_backtick_in_the_info_string_is_not_a_fence():
    md = "```python`x\nprint(1)\n```\n"
    assert extract_examples(md) == []


def test_extract_examples_language_tag_is_lowercased():
    assert extract_examples("```Python\nprint(1)\n```\n") == [("python", "print(1)")]


def test_extract_examples_indented_fence_body_is_dedented_by_the_fence_indent():
    md = "1. Step\n\n   ```python\n   def f():\n       return 1\n   ```\n"
    assert extract_examples(md) == [("python", "def f():\n    return 1")]


def test_extract_examples_list_item_block_keeps_relative_indent():
    md = "1. step\n\n    ```python\n    for i in x:\n        print(i)\n    ```\n"
    assert extract_examples(md) == [("python", "for i in x:\n    print(i)")]


def test_extract_examples_space_before_language_tag_is_accepted():
    assert extract_examples("``` python\nprint(1)\n```\n") == [("python", "print(1)")]


def test_extract_examples_tab_fence_with_nested_indent_compiles():
    doc = "1. item\n\n\t```python\n\tif x:\n\t    y()\n\t```"
    [(lang, code)] = extract_examples(doc)
    assert code == "if x:\n    y()"
    compile(code, "<example>", "exec")


def test_explicit_null_memory_keeps_cpu_limit(monkeypatch):
    monkeypatch.setattr(process.sys, "platform", "linux")
    policy = ExecutionPolicy.from_config({"execution": {
        "backend": "subprocess", "allow_host": True, "mem_mb": None, "timeout": 3,
    }})
    runner = FakeRunner()
    result = run_example("python", "print(1)", policy, runner)
    assert result.status == "passed", result.error
    assert runner.calls[0].limits == Limits(cpu_sec=3, memory_bytes=None)
    assert ExecutionPolicy.from_config({}).mem_mb == 512


@pytest.mark.parametrize("mem_mb", [None, 0, -1])
def test_docker_requires_positive_memory_cap(mem_mb):
    runner = FakeRunner()
    with pytest.raises(ExecutionRefused, match="positive memory cap"):
        run_example("python", "print(1)", ExecutionPolicy(backend="docker", mem_mb=mem_mb), runner)
    assert runner.calls == []


@pytest.mark.parametrize("platform,mem_mb,reason", [
    ("darwin", 512, "RLIMIT_AS"),
    ("win32", 512, "unsupported on Windows"),
    ("win32", None, "unsupported on Windows"),
])
def test_unsupported_host_caps_fail_before_launch(monkeypatch, platform, mem_mb, reason):
    monkeypatch.setattr(process.sys, "platform", platform)

    def forbidden(*args, **kwargs):
        pytest.fail("unsupported limits must refuse before launching a child")

    monkeypatch.setattr(process.subprocess, "Popen", forbidden)
    with pytest.raises(ExecutionRefused, match=reason):
        run_example("python", "raise AssertionError('must not execute')",
                    ExecutionPolicy(backend="subprocess", allow_host=True, mem_mb=mem_mb))


@pytest.mark.parametrize("platform,policy", [
    ("darwin", ExecutionPolicy(backend="subprocess", allow_host=True)),
    ("win32", ExecutionPolicy(backend="subprocess", allow_host=True)),
    ("linux", ExecutionPolicy(backend="docker", mem_mb=None)),
])
def test_refused_limits_stop_scoring_instead_of_a_perfect_rate(tmp_path, monkeypatch, platform, policy):
    monkeypatch.setattr(process.sys, "platform", platform)
    doc = tmp_path / "d.md"
    doc.write_text("```python\nimport package_that_does_not_exist\n```\n", encoding="utf-8")
    with pytest.raises(ExecutionRefused, match="python example not run: "):
        score_examples(doc, policy)


def test_subprocess_refuses_a_non_positive_memory_cap():
    runner = FakeRunner()
    with pytest.raises(ExecutionRefused, match="mem_mb must be positive"):
        run_example("python", "print(1)",
                    ExecutionPolicy(backend="subprocess", allow_host=True, mem_mb=0), runner)
    assert runner.calls == []


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is enforced on Linux")
def test_subprocess_memory_cap_is_enforced():
    res = run_example("python", "x = bytearray(900 * 1024 * 1024)",
                      ExecutionPolicy(backend="subprocess", allow_host=True, mem_mb=400, timeout=10))
    assert res.status == "failed" and "Exit code" in (res.error or "")
