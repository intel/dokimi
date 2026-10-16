# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What an agent's context window held during a run, read from the harness trace."""

from __future__ import annotations

from dokimi.context.trace import ContextTrace, TraceError, read_trace

__all__ = ["ContextTrace", "TraceError", "read_trace"]
