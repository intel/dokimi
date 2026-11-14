# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Each reader of the shared project-marker names matches the same set it matched before they were shared.

The expected sets are the literal lists the readers carried before ``dokimi/repos/project_markers.py``.
"""

from dokimi.repos import project_markers
from dokimi.repos.environment import checks
from dokimi.repos.l0 import adapters, core


def test_project_roots_marker_set_is_unchanged():
    assert checks.PROJECT_MARKERS == frozenset({
        "package.json", "Makefile", "makefile", "GNUmakefile", "pyproject.toml", "setup.py",
        "setup.cfg", "Pipfile", "go.mod", "Cargo.toml",
    })
    assert project_markers.PROJECT_MARKERS is checks.PROJECT_MARKERS


def test_python_gate_names_are_unchanged():
    assert set(project_markers.PYTHON_PROJECT_MARKERS) == {"pyproject.toml", "setup.py", "setup.cfg", "Pipfile"}


def test_make_lookup_order_is_unchanged():
    # GNU make's order: the first one present is the makefile the make check reads
    assert project_markers.MAKEFILE_NAMES == ("GNUmakefile", "makefile", "Makefile")


def test_make_adapter_detect_set_is_unchanged():
    assert set(adapters._DETECT["make"]) == {"Makefile", "makefile", "GNUmakefile"}


def test_root_files_set_is_unchanged():
    assert core.ROOT_FILES == frozenset({
        "Makefile", "makefile", "GNUmakefile", "CMakeLists.txt", "MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel",
        "BUILD", "BUILD.bazel", ".bazelrc", ".bazelversion", "pyproject.toml", "setup.py", "setup.cfg",
        "conftest.py", "pytest.ini", "tox.ini", "requirements.txt", ".pre-commit-config.yaml", ".clang-format",
        ".editorconfig", ".gitignore", "Dockerfile",
    })


def test_environment_checks_read_the_shared_makefile_names():
    # the runner check looked for `include` lines in a second copy of the makefile names; it reads the shared one
    from dokimi.repos import environment

    assert "_ROOT_MAKEFILES" not in vars(environment)
    assert environment.MAKEFILE_NAMES is project_markers.MAKEFILE_NAMES


def test_runner_check_reads_a_lowercase_makefile(tmp_path):
    # a lowercase makefile with an include line is read by the runner check, not only `Makefile`
    from dokimi.repos import environment
    from dokimi.repos.l0.core import Scan
    from dokimi.repos.source import WorkTree

    (tmp_path / "makefile").write_text("include common.mk\n", encoding="utf-8")
    assert environment._runner_file_unread(Scan(WorkTree(tmp_path), frozenset()))


def _checks(tmp_path, files):
    from dokimi.repos.l0.core import Scan
    from dokimi.repos.source import WorkTree

    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return checks.detect_checks(Scan(WorkTree(tmp_path), frozenset()))


def test_make_check_reads_gnumakefile_before_makefile(tmp_path):
    # GNU make reads GNUmakefile first, so the make check takes its targets from there, not from Makefile
    found = _checks(tmp_path, {"GNUmakefile": "check:\n", "Makefile": "lint:\n"})
    assert {c.evidence for c in found if c.ecosystem == "make"} == {"GNUmakefile"}


def test_python_checks_open_on_a_pipfile_alone(tmp_path):
    # a root whose only Python manifest is a Pipfile still gets its tox.ini test check
    found = _checks(tmp_path, {"Pipfile": "[packages]\n", "tox.ini": "[tox]\n"})
    assert [c.kind for c in found if c.ecosystem == "python"] == ["test"]


def test_make_target_scan_reads_a_lowercase_makefile(tmp_path):
    # the l0 make facts take targets from a lowercase makefile, not only from Makefile
    from dokimi.repos.l0.core import Scan
    from dokimi.repos.source import WorkTree

    (tmp_path / "makefile").write_text("onlylower:\n", encoding="utf-8")
    assert "onlylower" in adapters._make_facts(Scan(WorkTree(tmp_path), frozenset()))["targets"]
