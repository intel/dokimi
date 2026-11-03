# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A caller of the core classes."""

from sample_pkg.core import Impl, helper


def use(value):
    obj = Impl()
    return obj.run(helper(value))
