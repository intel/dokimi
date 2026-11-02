# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An implementation of Base, and a helper it calls."""

from sample_pkg.base import Base


class Impl(Base):
    """Overrides Base.run."""

    def run(self, value):
        total = helper(value)
        return total


def helper(value):
    return value + 1
