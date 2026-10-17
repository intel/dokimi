# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The dokimi version, single-sourced from ``project.version`` in pyproject.toml.

A source checkout reads pyproject.toml directly, so the version is right even
when the tree is used without installing it (``python cli.py``) or after a bump
that an editable install's metadata has not caught up with. An installed wheel
has no pyproject.toml next to the package and reads the distribution metadata.
"""

from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _read_version() -> str:
    if _PYPROJECT.is_file():
        project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8")).get("project", {})
        if project.get("name") == "dokimi" and project.get("version"):
            return str(project["version"])
    try:
        return metadata.version("dokimi")
    except metadata.PackageNotFoundError:
        return "0+unknown"


__version__ = _read_version()
