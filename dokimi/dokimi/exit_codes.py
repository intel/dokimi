# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exit code of an input or usage refusal (the exit-code table is in docs/quickstart.md).

A bad input file, an input the command cannot use, a flag combination it refuses and a
missing credential all exit 2. Exit 1 is for a verdict or a gate that ran and failed.
"""

from __future__ import annotations

EXIT_USAGE = 2
#: Exit code of a run that ran and failed: a model call refused, a judge or a gate that failed.
EXIT_FAILURE = 1
#: Exit code of an inconclusive result: a run that stopped short (a budget stop), or an undecided one that
#: ran to the end (issue #94 §2.6). Not a verdict and not a bad input.
EXIT_INCONCLUSIVE = 3
#: Exit code of a result that is not comparable: a model that differs from the baseline's, or one that
#: cannot be verified (issue #94 §2.6).
EXIT_INVALID = 4
#: Exit code of an infrastructure failure: an uncaught exception (a crash never reads as a verdict).
EXIT_INFRA = 5

