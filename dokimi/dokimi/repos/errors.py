"""Errors shared by the repos modules; a leaf, so any of them can import it."""

from __future__ import annotations


class PrepError(RuntimeError):
    """A workspace could not be prepared (a patch does not apply, a base is missing, ...)."""
