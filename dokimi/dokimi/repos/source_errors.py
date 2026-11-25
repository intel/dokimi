# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The errors of :mod:`dokimi.repos.source`, apart so that :mod:`dokimi.repos.links` can raise them."""

from __future__ import annotations


class SourceError(ValueError):
    """Raised for an unusable source spec or a git operation that failed."""


class _NoSuchPath(SourceError):
    """A path walks up out of a directory the tree does not have, which a checkout answers with ENOENT."""


class SourceArgumentError(SourceError):
    """A source or a path that is wrong as given or as laid out: a local path that is not the repository
    root, a path that leaves the tree, a link that goes nowhere. No git operation failed, so a caller
    reports it as a usage error; a SourceError that is not this one is a failed clone, fetch or `git show`."""
