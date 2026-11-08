### Fixed

- **`catalog eval` report counts its selection in rows and in names.** The
  `Selected skills` headline counted distinct skill names (1,741 on a catalog of
  2,105 `catalog check` rows), while the table listed every row as selected. The
  headline now reads `N of M rows (K distinct names)`, states the rule (selection is
  by skill name; nested and load-error rows are candidates; rows that share a name
  with an earlier row are selected with it), and gives the excluded rows and why
  (none in a full run; in `--changed-from`, rows whose skill no changed path owns).
  The selection itself and `catalog.json` are unchanged.
