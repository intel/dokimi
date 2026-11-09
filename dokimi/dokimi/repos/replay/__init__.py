# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval tasks generate --from replay`` and ``repo tasks authoring``: tasks from merged pull requests
(``dokimi:review_replay`` from their review threads, ``dokimi:hidden_tests`` from their diffs).

:mod:`.threads` reads the pull requests and picks the gold threads, :mod:`.history` asks
git where a line went and whether the PR's own commits changed it, and :mod:`.build`
writes one ``dokimi:review_replay`` task directory per pull request. :mod:`.authoring`
writes one ``dokimi:hidden_tests`` task per change, and :mod:`.authoring_prs` makes a
change of each merged pull request; :mod:`.merged` holds what both kinds share (the base,
the files left out). No GitHub call is made: the pull requests come from a file
``gh api graphql`` wrote, so the commands work offline and on a private repository alike.
"""

from .build import DEFAULT_PROMPT, Options, Outcome, build_all, task_id
from .history import History
from .threads import PullRequest, ReplayError, load_pull_requests, read_threads

__all__ = ["DEFAULT_PROMPT", "History", "Options", "Outcome", "PullRequest", "ReplayError", "build_all",
           "load_pull_requests", "read_threads", "task_id"]
