# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The text of a ``repo compare`` row (:mod:`.compare`): its table cells and its 95% interval.

The interval is every shift the row's sign-flip test does not reject: :func:`dokimi.eval.sign_flip.sign_flip_interval`
computes it, and its module docstring gives the method. This module holds what a row prints and checks of it.
"""

from __future__ import annotations

from typing import Any

#: The table's columns: a header, its alignment and its least width. A wider cell widens its column in every table.
COLUMNS = (("measure", "<", 7), ("tasks", ">", 5), ("baseline", ">", 9), ("treatment", ">", 9),
           ("delta [95% CI]", "<", 26), ("p", ">", 6), ("p_holm", ">", 6))


def table_cells(row: dict[str, Any]) -> list[str]:
    """The cells of one ``repo compare`` row: one per :data:`COLUMNS` entry, then ``up/down/tied``."""
    d = row["delta"]
    delta = format_value(d["mean"], signed=True) + " " + format_interval(d["lo"], d["hi"])
    p_texts = ["-" if p is None else f"{p:.3f}" for p in (row["p"], row["p_holm"])]
    return [row["measure"], str(row["n_tasks"]), format_value(row["baseline_mean"]),
            format_value(row["treatment_mean"]), delta, *p_texts, f"{row['up']}/{row['down']}/{row['tied']}"]


def column_widths(rows: list[list[str]]) -> list[int]:
    """Each column's width over every table: its least width, or its widest cell."""
    return [max([least] + [len(cells[i]) for cells in rows]) for i, (_, _, least) in enumerate(COLUMNS)]


def table_line(cells: list[str], widths: list[int]) -> str:
    """One line of a table, each cell padded to its column's width and the last one unpadded."""
    columns = zip(cells[:-1], COLUMNS, widths, strict=True)
    padded = (f"{text:{align}{width}}" for text, (_, align, _), width in columns)
    return "  " + "  ".join([*padded, cells[-1]])


def includes_zero(lo: float | None, hi: float | None) -> bool:
    """Whether the interval holds 0, an open end unbounded."""
    return (lo is None or lo <= 0) and (hi is None or hi >= 0)


def format_interval(lo: float | None, hi: float | None) -> str:
    """``[lo, hi]`` at 3 decimals, an open end as ``-inf`` or ``+inf``; a non-zero end 3 decimals would print as
    ``0.000`` gets 2 significant digits, so the text shows the side of 0 the row's verdict puts it on."""
    low = "-inf" if lo is None else format_value(lo, signed=True)
    high = "+inf" if hi is None else format_value(hi, signed=True)
    return f"[{low}, {high}]"


def format_value(value: float | None, signed: bool = False) -> str:
    """``value`` at 3 decimals, ``-`` for ``None``; a non-zero value 3 decimals would print as ``0.000`` gets 2
    significant digits (``1e-12``, ``-1e-12``) and keeps its sign. ``signed`` always writes the sign."""
    if value is None:
        return "-"
    value += 0.0  # no -0.000
    text = f"{value:+.3f}" if signed else f"{value:.3f}"
    if value and float(text) == 0:
        return f"{value:+.2g}" if signed else f"{value:.2g}"
    return text
