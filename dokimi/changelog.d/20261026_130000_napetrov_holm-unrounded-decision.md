### Fixed

- **Significance after Holm or BH is decided on the unrounded adjusted p** — the
  verdict (`significant_adjusted`) of the arms report, the interaction contrasts
  and the model-comparison report was tested against alpha on the 4-decimal
  rounded `p_adjusted`, so an adjusted p just under 0.05 showed as 0.05 and read as
  not significant. It now uses the exact value, as the cross-judge Holm and the
  harness McNemar verdicts already did. `p_adjusted` still displays 4 decimals.

### Changed

- **Some Holm and BH verdicts at the boundary change from not significant to
  significant** — a result whose exact adjusted p is in `[0.04995, 0.05)` is now
  `significant_adjusted: true`, where it was `false`. Example: three tests at
  p = 0.0166662 give a Holm p of 0.0499986, shown as `0.05`; the arms report now
  marks them significant, and so does the model-comparison report, whose Holm ran
  on p-values already rounded to 4 decimals (0.0501 here). Its Wilcoxon clause is
  read unrounded too, as in the arms report. No verdict moves the other way, and
  results outside that window keep their verdict. Reports written by earlier
  versions keep their old verdicts; re-run the comparison to get the new ones.
- **The model-comparison `p (adjusted)` column can move in its last decimal** —
  it is now the exact adjusted p rounded once, as in the arms report: three runs
  at a raw p of 0.024037 showed `0.072` (3 x 0.024) and now show `0.0721`.
