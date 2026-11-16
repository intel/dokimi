# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Manifest and makefile names that more than one repository reader matches on.

The environment checks and the l0 scan both look for these by name; one list each keeps them from drifting apart.
"""

#: GNU make's lookup order, first one present wins.
MAKEFILE_NAMES = ("GNUmakefile", "makefile", "Makefile")
#: The Python manifests that declare a build, a test or a lint configuration.
PYTHON_MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg")
#: What makes a directory a Python project: a manifest, or a Pipfile.
PYTHON_PROJECT_MARKERS = (*PYTHON_MANIFESTS, "Pipfile")
#: A directory holding one of these is a project of its own: its scripts and config apply there.
PROJECT_MARKERS = frozenset({"package.json", "go.mod", "Cargo.toml", *MAKEFILE_NAMES, *PYTHON_PROJECT_MARKERS})
