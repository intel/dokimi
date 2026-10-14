# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The version is single-sourced and the wheel carries every file the package reads.

The CI ``package-smoke`` job proves the same on a built wheel; these tests catch
it earlier, without a build.
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from pathlib import Path

import pytest

import dokimi
from dokimi import config, intents, registry
from dokimi.cli import build_parser
from dokimi.runner import spec

PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT / "dokimi"
PYPROJECT = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_comes_from_pyproject():
    assert dokimi.__version__ == PYPROJECT["project"]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+((a|b|rc)\d+)?(\.dev\d+)?", dokimi.__version__)


def test_cli_prints_the_version(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"dokimi {dokimi.__version__}"


def _shipped(path: Path) -> bool:
    rel = path.relative_to(PACKAGE).as_posix()
    globs = PYPROJECT["tool"]["setuptools"]["package-data"]["dokimi"]
    return path.suffix == ".py" or any(fnmatch.fnmatch(rel, g) for g in globs)


@pytest.mark.parametrize("path", [
    registry._DEFAULT_REGISTRY,
    intents._DEFAULT_INTENTS,
    config.DEFAULT_PRODUCTS_PATH,
    spec.DEFAULT_SCHEMA_PATH,
], ids=lambda p: p.name)
def test_default_data_files_ship_in_the_wheel(path):
    assert path.is_file()
    assert path.resolve().is_relative_to(PACKAGE)
    assert _shipped(path.resolve())


def test_every_non_python_file_in_the_package_ships():
    stray = [
        p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and not _shipped(p)
    ]
    assert not stray, f"add these to [tool.setuptools.package-data] or move them out: {stray}"


@pytest.mark.parametrize("name", ["LICENSE", "NOTICE"])
def test_license_files_match_the_repository_root(name):
    # pyproject's license-files cannot reach outside the project directory, so
    # the project keeps copies of the repository-root files.
    assert (PROJECT / name).read_bytes() == (PROJECT.parent / name).read_bytes()
