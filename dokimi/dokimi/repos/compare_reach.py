# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""How small an effect a ``repo compare`` row can find: ``mde``, an uncorrected t-based planning estimate, and the
lowest ``p_holm`` Holm's family leaves a gate or score row (:mod:`.compare`)."""

from __future__ import annotations

import math

from scipy import stats as sp_stats

from dokimi.eval.sign_flip import ALPHA
from dokimi.eval.stats import correct_family

#: The power ``mde`` is sized for, at :data:`~dokimi.eval.sign_flip.ALPHA`, the level the notes read p at.
POWER = 0.80


def min_detectable(sd: float, n: int) -> float:
    """An approximate, uncorrected t-based estimate of the mean delta a two-sided one-sample t test at :data:`ALPHA`
    detects with :data:`POWER` over ``n`` tasks of spread ``sd``, a planning figure: not the power of the sign-flip
    test nor of the Holm-corrected decision. The quantiles are t at ``n - 1`` degrees of freedom, those of the
    planning figure: a row's interval inverts the sign-flip test and has no quantiles (:mod:`dokimi.eval.sign_flip`)."""
    df = n - 1
    return (sp_stats.t.ppf(1 - ALPHA / 2, df) + sp_stats.t.ppf(POWER, df)) * sd / math.sqrt(n)


def holm_floor(pvals: list[float | None], i: int, floor: float) -> tuple[float | None, bool | None]:
    """Holm's p of row ``i`` of the family ``pvals`` with its p put at ``floor``, the lowest it can go, and the
    others as observed; and whether that p is below :data:`ALPHA`. Where it is not, no effect can be."""
    return correct_family([*pvals[:i], floor, *pvals[i + 1:]])[i]
