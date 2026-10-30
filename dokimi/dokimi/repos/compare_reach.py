# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""How small an effect a ``repo compare`` row can find: ``mde``, and the lowest ``p_holm`` Holm's family leaves a
gate or score row (:mod:`.compare`)."""

from __future__ import annotations

import math

from scipy import stats as sp_stats

from dokimi.eval.stats import correct_family

#: The level the notes read p and the interval at (two-sided), and the power ``mde`` is sized for.
ALPHA = 0.05
POWER = 0.80


def min_detectable(sd: float, n: int) -> float:
    """The smallest mean delta a two-sided test at :data:`ALPHA` finds with :data:`POWER` over ``n`` tasks of
    spread ``sd``. The quantiles are t at ``n - 1`` degrees of freedom, as the interval of ``mean_ci`` is."""
    df = n - 1
    return (sp_stats.t.ppf(1 - ALPHA / 2, df) + sp_stats.t.ppf(POWER, df)) * sd / math.sqrt(n)


def holm_floor(pvals: list[float | None], i: int, floor: float) -> tuple[float | None, bool | None]:
    """Holm's p of row ``i`` of the family ``pvals`` with its p put at ``floor``, the lowest it can go, and the
    others as observed; and whether that p is below :data:`ALPHA`. Where it is not, no effect can be."""
    return correct_family([*pvals[:i], floor, *pvals[i + 1:]])[i]
