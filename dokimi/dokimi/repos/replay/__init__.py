# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo tasks replay``: ``dokimi:review_replay`` tasks from recorded review threads.

:mod:`.threads` reads the pull requests and picks the gold threads, :mod:`.history` asks
git where a line went and whether the PR's own commits changed it, and :mod:`.build`
writes one task directory per pull request. No GitHub call is made: the threads come
from a file ``gh api graphql`` wrote, so the command works offline and on a private
repository alike.
"""

from .build import DEFAULT_PROMPT, Options, Outcome, build_all, task_id
from .history import History
from .threads import PullRequest, ReplayError, load_pull_requests, read_threads

__all__ = ["DEFAULT_PROMPT", "History", "Options", "Outcome", "PullRequest", "ReplayError", "build_all",
           "load_pull_requests", "read_threads", "task_id"]
