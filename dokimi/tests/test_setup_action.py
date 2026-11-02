# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""actions/setup/action.yml: no pip cache, and an out-dir newline is refused before it is created.

A pip cache restored in a run that checks out the caller's dokimi-ref is the cache-poisoning path
CodeQL reports (actions/cache-poisoning/poisonable-step), so setup-python runs without a cache.
A newline in out-dir is refused before mkdir; the captured path is written without a newline.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

ACTION = Path(__file__).resolve().parents[2] / "actions/setup/action.yml"


def _steps() -> list[dict]:
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    return action["runs"]["steps"]


def test_setup_python_runs_without_a_cache() -> None:
    setup_python = [s for s in _steps() if str(s.get("uses", "")).startswith("actions/setup-python@")]
    assert len(setup_python) == 1
    assert "cache" not in setup_python[0].get("with", {})
    assert "cache" not in yaml.safe_load(ACTION.read_text(encoding="utf-8"))["inputs"]


def _run_out_step(tmp_path: Path, out_dir: str, cwd: Path | None = None, prelude: str = "") -> subprocess.CompletedProcess[str]:
    # Runs the output step's script the way the runner does: bash -e -o pipefail, with the
    # runner's env files and RUNNER_TEMP pointed at tmp_path.
    script = next(s["run"] for s in _steps() if s.get("id") == "out")
    env = {
        "PATH": os.environ["PATH"],
        "RUNNER_TEMP": str(tmp_path / "runner-temp"),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_ENV": str(tmp_path / "env"),
        "OUT": out_dir,
    }
    (tmp_path / "runner-temp").mkdir()
    for name in ("output", "env"):
        (tmp_path / name).touch()
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", prelude + script],
        env=env, capture_output=True, text=True, check=False, cwd=cwd or tmp_path,
    )


def test_out_dir_default_is_written_as_a_clean_path(tmp_path: Path) -> None:
    result = _run_out_step(tmp_path, "")
    assert result.returncode == 0, result.stderr
    output = (tmp_path / "output").read_text(encoding="utf-8")
    expected = (tmp_path / "runner-temp" / "dokimi-out").resolve()
    assert output == f"dir={expected}\n"


def test_out_dir_starting_with_a_dash_is_a_path_not_an_option(tmp_path: Path) -> None:
    result = _run_out_step(tmp_path, "-reports")
    assert result.returncode == 0, result.stderr
    expected = (tmp_path / "-reports").resolve()
    assert (tmp_path / "output").read_text(encoding="utf-8") == f"dir={expected}\n"


def test_out_dir_of_a_lone_dash_is_the_directory_not_oldpwd(tmp_path: Path) -> None:
    result = _run_out_step(tmp_path, "-")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "output").read_text(encoding="utf-8") == f"dir={(tmp_path / '-').resolve()}\n"


def test_out_dir_of_a_lone_dash_with_oldpwd_set(tmp_path: Path) -> None:
    # OLDPWD is set by a leading cd in the script, as it would be after any earlier cd.
    script_cwd = tmp_path / "work"
    script_cwd.mkdir()
    result = _run_out_step(tmp_path, "-", cwd=script_cwd, prelude="cd /tmp\ncd -- \"$OLDPWD\"\n")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "output").read_text(encoding="utf-8") == f"dir={(script_cwd / '-').resolve()}\n"


def test_a_cwd_ending_in_a_newline_is_refused(tmp_path: Path) -> None:
    # The path that reaches GITHUB_OUTPUT must not lose a newline at its end.
    cwd = tmp_path / "work\n"
    cwd.mkdir()
    result = _run_out_step(tmp_path, ".", cwd=cwd)
    assert result.returncode != 0
    assert "out-dir has a newline" in result.stdout
    assert (tmp_path / "output").read_text(encoding="utf-8") == ""


def test_out_dir_with_a_newline_is_refused_before_mkdir(tmp_path: Path) -> None:
    result = _run_out_step(tmp_path, "reports\n")
    assert result.returncode != 0
    assert "out-dir has a newline" in result.stdout
    assert not any(p.name.startswith("reports") for p in tmp_path.iterdir())
    assert (tmp_path / "output").read_text(encoding="utf-8") == ""


TAG_OBJECT = "a" * 40  # the annotated tag's own object
PEELED = "b" * 40  # the commit the tag names (its ^{} line)


def _resolve_ref(tmp_path: Path, ref: str) -> tuple[subprocess.CompletedProcess[str], str, str]:
    # Runs the ref step the way the runner does, with a fake git that logs its calls and lists a tag.
    # Returns (result, the git calls logged, the sha written to GITHUB_OUTPUT).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "git.log"
    fake = bin_dir / "git"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{log}'\n"
        f"printf '%s\\trefs/tags/release\\n%s\\trefs/tags/release^{{}}\\n%s\\trefs/tags/releases/v1\\n%s\\trefs/tags/v1.2.3+build.1\\n' '{TAG_OBJECT}' '{PEELED}' '{PEELED}' '{PEELED}'\n",
        encoding="utf-8")
    fake.chmod(0o755)
    output = tmp_path / "output"
    output.touch()
    script = next(s["run"] for s in _steps() if s.get("id") == "ref")
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "REF": ref,
           "DOKIMI_REPO": "intel/dokimi", "GITHUB_OUTPUT": str(output)}
    result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                            env=env, capture_output=True, text=True, check=False, cwd=tmp_path)
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return result, calls, output.read_text(encoding="utf-8")


def test_a_commit_ref_is_installed_as_given_and_the_tag_is_not_looked_up_again(tmp_path: Path) -> None:
    # repo-eval passes the commit it resolved, so a tag that moved since cannot change what is installed.
    result, calls, output = _resolve_ref(tmp_path, PEELED)
    assert result.returncode == 0, result.stderr
    assert calls == ""
    assert output == f"sha={PEELED}\n"


def test_an_uppercase_commit_ref_is_lowercased(tmp_path: Path) -> None:
    result, calls, output = _resolve_ref(tmp_path, PEELED.upper())
    assert result.returncode == 0, result.stderr
    assert calls == ""
    assert output == f"sha={PEELED}\n"


def test_a_tag_ref_resolves_to_the_commit_it_names(tmp_path: Path) -> None:
    result, calls, output = _resolve_ref(tmp_path, "release")
    assert result.returncode == 0, result.stderr
    assert "ls-remote --tags" in calls
    assert output == f"sha={PEELED}\n"


def test_a_tag_ref_with_a_slash_resolves(tmp_path: Path) -> None:
    result, calls, output = _resolve_ref(tmp_path, "releases/v1")
    assert result.returncode == 0, result.stderr
    assert "ls-remote --tags" in calls
    assert output == f"sha={PEELED}\n"


def test_a_tag_ref_with_a_plus_resolves(tmp_path: Path) -> None:
    result, calls, output = _resolve_ref(tmp_path, "v1.2.3+build.1")
    assert result.returncode == 0, result.stderr
    assert "ls-remote --tags" in calls
    assert output == f"sha={PEELED}\n"


def test_the_action_declares_no_paid_or_max_usd_input() -> None:
    # A spending cap the action does not enforce must not ship as a safety input.
    inputs = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["inputs"]
    assert "paid" not in inputs
    assert "max-usd" not in inputs


def test_the_action_exports_no_dokimi_runs_dir_or_max_usd() -> None:
    # DOKIMI_RUNS_DIR is read only by `dokimi runs`; the writers ignore it, so exporting it
    # misleads. DOKIMI_MAX_USD is read by no code. Neither is exported or documented here.
    text = ACTION.read_text(encoding="utf-8")
    assert "DOKIMI_RUNS_DIR" not in text
    assert "DOKIMI_MAX_USD" not in text


def test_the_install_step_installs_the_resolved_commit_with_its_locked_dependencies() -> None:
    # Without the resolved SHA a moved tag installs another commit; without --require-hashes the lock is not verified.
    install = next(s for s in _steps() if s.get("name") == "Install dokimi")
    assert install["env"]["SHA"] == "${{ steps.ref.outputs.sha }}"
    assert ('pip install --require-hashes -r "https://raw.githubusercontent.com/$DOKIMI_REPO/$SHA/'
            'dokimi/requirements.lock.txt"') in install["run"]
    assert 'pip install --no-deps "dokimi @ git+' in install["run"]
    assert "@$SHA#subdirectory=dokimi" in install["run"]
