# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What the terminating-signal handler of :mod:`dokimi.process` runs after it has killed the
child process groups (#94 R2).

The handler :func:`dokimi.process.install_signal_handlers` installs (SIGTERM, SIGHUP) defers a
signal that lands while a child is being registered, kills every live group, then calls the hooks
added here, newest first, and only then hands the signal on. A hook may raise: a run directory
raises :class:`dokimi.process.ShuttingDown` to unwind its run and record the stop, and once it is
done delivers the signal again (``signal.raise_signal``), so the process ends as it would have.
Add and remove hooks on the main thread, where Python runs signal handlers.
"""

from __future__ import annotations

from collections.abc import Callable

_hooks: list[Callable[[int], None]] = []


def add(hook: Callable[[int], None]) -> None:
    """Call ``hook(signum)`` on a terminating signal, until :func:`remove`."""
    _hooks.append(hook)


def remove(hook: Callable[[int], None]) -> None:
    """Stop calling ``hook``; ``ValueError`` if it was not added."""
    _hooks.remove(hook)


def run(signum: int) -> None:
    """Call every hook with ``signum``, the newest first; a hook that raises stops the rest."""
    for hook in reversed(list(_hooks)):
        hook(signum)
