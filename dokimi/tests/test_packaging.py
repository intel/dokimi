# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The version is single-sourced and the wheel carries every file the package reads.

The CI ``package-smoke`` job proves the same on a built wheel; these tests catch
it earlier, without a build.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

import dokimi
from dokimi import _version, config, intents, registry
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


def _metadata_version(name):
    if name == "dokimi":
        return "9.8.7"
    raise _version.metadata.PackageNotFoundError(name)


def test_version_without_pyproject_reads_the_metadata(tmp_path, monkeypatch):
    # An installed wheel: no pyproject.toml next to the package.
    monkeypatch.setattr(_version, "_PYPROJECT", tmp_path / "pyproject.toml")
    monkeypatch.setattr(_version.metadata, "version", _metadata_version)
    assert _version._read_version() == "9.8.7"


def test_the_source_pyproject_wins_over_stale_metadata(tmp_path, monkeypatch):
    # An editable install whose metadata predates a version bump.
    ours = tmp_path / "pyproject.toml"
    ours.write_text('[project]\nname = "dokimi"\nversion = "1.2.3"\n', encoding="utf-8")
    monkeypatch.setattr(_version, "_PYPROJECT", ours)
    monkeypatch.setattr(_version.metadata, "version", _metadata_version)
    assert _version._read_version() == "1.2.3"


def test_version_ignores_another_projects_pyproject(tmp_path, monkeypatch):
    other = tmp_path / "pyproject.toml"
    other.write_text('[project]\nname = "other"\nversion = "1.0"\n', encoding="utf-8")
    monkeypatch.setattr(_version, "_PYPROJECT", other)

    def missing(name):
        raise _version.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(_version.metadata, "version", missing)
    assert _version._read_version() == "0+unknown"


def test_a_local_runtime_config_overrides_the_bundled_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert config.runtime_config_path() == config.DEFAULT_PRODUCTS_PATH
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "products.yaml").write_text("llm: {}\n", encoding="utf-8")
    assert config.runtime_config_path() == config.LOCAL_PRODUCTS_PATH


def _package_data(package: Path) -> set[Path]:
    """The files the package-data globs pick up. Path.glob, like setuptools and
    unlike fnmatch, does not let ``*`` match across ``/``."""
    globs = PYPROJECT["tool"]["setuptools"]["package-data"]["dokimi"]
    return {p.resolve() for g in globs for p in package.glob(g) if p.is_file()}


def _stray(package: Path) -> list[str]:
    shipped = _package_data(package)
    return sorted(
        p.relative_to(package).as_posix() for p in package.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
        and p.suffix != ".py" and p.resolve() not in shipped
    )


@pytest.mark.parametrize("path", [
    registry._DEFAULT_REGISTRY,
    intents._DEFAULT_INTENTS,
    config.DEFAULT_PRODUCTS_PATH,
    spec.DEFAULT_SCHEMA_PATH,
], ids=lambda p: p.name)
def test_default_data_files_ship_in_the_wheel(path):
    assert path.is_file()
    assert path.resolve().is_relative_to(PACKAGE)
    assert path.resolve() in _package_data(PACKAGE)


def test_every_non_python_file_in_the_package_ships():
    stray = _stray(PACKAGE)
    assert not stray, f"add these to [tool.setuptools.package-data] or move them out: {stray}"


def test_a_data_file_one_level_too_deep_is_stray(tmp_path):
    for rel in ("schemas/x.json", "schemas/v2/x.json", "config/sub/x.yaml"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("{}", encoding="utf-8")
    assert _stray(tmp_path) == ["config/sub/x.yaml", "schemas/v2/x.json"]


@pytest.mark.parametrize("name", ["LICENSE", "NOTICE"])
def test_license_files_match_the_repository_root(name):
    # pyproject's license-files cannot reach outside the project directory, so
    # the project keeps copies of the repository-root files.
    assert (PROJECT / name).read_bytes() == (PROJECT.parent / name).read_bytes()


class _Resolved(Exception):
    pass


@pytest.mark.parametrize("module, func", [
    ("dokimi.commands.questions", "cmd_questions_generate"),
    ("dokimi.commands.answers", "cmd_answers_generate"),
])
def test_generate_commands_read_the_resolved_runtime_config(monkeypatch, module, func):
    # The installed-wheel case: the config comes from runtime_config_path(),
    # not from a hard-coded config/products.yaml in the working directory.
    import importlib
    import types

    import dokimi.config as cfg

    def spy():
        raise _Resolved

    monkeypatch.setattr(cfg, "runtime_config_path", spy)
    args = types.SimpleNamespace(product="p", top_k=None)
    with pytest.raises(_Resolved):
        getattr(importlib.import_module(module), func)(args)
