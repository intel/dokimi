#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""CLI for dokimi: thin compatibility shim.

The implementation now lives in the ``dokimi`` package. This module
keeps ``python cli.py ...`` working. Only ``main`` and ``build_parser`` are
re-exported here; import command functions from ``dokimi.commands.*``.
"""

from __future__ import annotations

from dokimi.cli import build_parser, main

__all__ = ["build_parser", "main"]

if __name__ == "__main__":
    main()
