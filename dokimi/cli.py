#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""CLI for dokimi: thin compatibility shim.

The implementation now lives in the ``dokimi`` package. This module
keeps ``python cli.py ...`` working. Only ``main``, ``build_parser`` and
``parse_args`` are re-exported here; import command functions from
``dokimi.commands.*``. Parse with ``parse_args``: ``build_parser().parse_args()``
leaves defaults that hang on another flag (``--judge-model`` on
``--judge-provider``) unresolved.
"""

from __future__ import annotations

from dokimi.cli import build_parser, main, parse_args

__all__ = ["build_parser", "main", "parse_args"]

if __name__ == "__main__":
    main()
