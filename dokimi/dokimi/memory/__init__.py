# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Agent memory as an evaluation subject: read a harness's memory store, then check it.

The first reader is Claude Code auto memory (``~/.claude/projects/<project>/memory/``):
a ``MEMORY.md`` index loaded at every session start and one topic file per memory.
Tracking issue: https://github.com/intel/dokimi/issues/728
"""

from dokimi.memory.store import MemoryEntry, MemoryStore, MemoryStoreError, read_claude_memory

__all__ = ["MemoryEntry", "MemoryStore", "MemoryStoreError", "read_claude_memory"]
