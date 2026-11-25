# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The finding codes of ``catalog check --upstream`` (see :mod:`dokimi.external.upstream_findings`).

A leaf module: :data:`dokimi.external.validate.FINDING_CODES` names them, and the findings module imports the validator,
so the codes cannot live in the findings module without an import cycle.
"""

UPSTREAM_FINDING_CODES = (
    "license-dropped-from-upstream",
    "risk-inherited-from-upstream",
    "upstream-stale",
)
