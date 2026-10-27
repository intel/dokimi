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
  marks them significant. No verdict moves the other way, and results outside that
  window keep their verdict. Reports written by earlier versions keep their old
  verdicts; re-run the comparison to get the new ones.
