# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exit code of an input or usage refusal (the exit-code table is in docs/quickstart.md).

A bad input file, an input the command cannot use, a flag combination it refuses and a
missing credential all exit 2. Exit 1 is for a verdict or a gate that ran and failed.
"""

from __future__ import annotations

import sys
from typing import NoReturn

EXIT_USAGE = 2
#: Exit code of a run that ran and failed: a model call refused, a judge or a gate that failed.
EXIT_FAILURE = 1


def usage_exit(message: str) -> NoReturn:
    """Print ``message`` to stderr and exit :data:`EXIT_USAGE`.

    The raised ``SystemExit`` keeps ``message`` as its text, so an in-process caller can read it
    with ``str()``; its ``code`` is still the status.
    """
    print(message, file=sys.stderr)
    error = SystemExit(message)
    error.code = EXIT_USAGE
    raise error
