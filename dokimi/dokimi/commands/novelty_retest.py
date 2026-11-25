# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""``skill novelty retest``: the noise floor from two analyze runs of the same items."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dokimi.artifacts import save_artifact
from dokimi.files import InputError


def cmd_novelty_retest(args: argparse.Namespace) -> None:
    """Noise floor from a retest: the same items analysed again with another seed and a fresh cache."""
    from dokimi.novelty.retest import (SMALL_SAMPLE_N, load_runs, pair_runs, pearson_text,
                                       render_markdown, retest_summary)

    paired = pair_runs(load_runs(Path(args.baseline)), load_runs(Path(args.retest)), args.scoring)
    for warning in paired["warnings"]:
        print(f"WARNING: {warning}", file=sys.stderr)
    if not paired["pairs"]:
        raise InputError("no item scored in both runs: there is nothing to measure a floor from")
    summary = retest_summary(paired["pairs"], args.scoring)
    summary.update(warnings=paired["warnings"], unmatched=paired["unmatched"],
                   unscored=paired["unscored"])
    if summary["floor"] is None:
        print(f"Noise floor not measured: {summary['n_items']} item scored in both runs (needs 2).")
    else:
        print(f"Noise floor ({args.scoring}): ±{summary['floor']:.3f} over {summary['n_items']} "
              f"items; Pearson r {pearson_text(summary)}; "
              f"mean |diff| {summary['mean_abs_diff']:.3f}; "
              f"mean shift {summary['mean_shift']:+.3f}; "
              f"{summary['n_below_floor']} of {summary['n_comparisons']} comparisons below the floor.")
        if summary["small_sample"]:
            print(f"Caveat: fewer than {SMALL_SAMPLE_N} items; read the floor as an order of magnitude.")
    if args.out_dir:
        out = Path(args.out_dir)
        if out.exists() and not out.is_dir():
            raise InputError(f"--out-dir {out} is a file, not a directory")
        for name in ("retest.json", "retest.md"):
            if (out / name).is_dir():
                raise InputError(f"{out / name} is a directory; remove it or pick another --out-dir")
        save_artifact("novelty_retest", summary, out / "retest.json")
        (out / "retest.md").write_text(render_markdown(summary, warnings=paired["warnings"]),
                                       encoding="utf-8")
        print(f"OK Saved retest report: {out / 'retest.md'}")
