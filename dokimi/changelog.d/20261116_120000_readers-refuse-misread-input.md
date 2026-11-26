### Fixed

- **Three readers no longer misread a malformed input** (#687):
  - A question set whose records share an id (`id`, or `question_id` in a v1 set; `1` and `"1"`
    count as one) is refused, naming the ids, before any answer is paid for. `answers generate`,
    `arms run`, `report generate`, `eval score reference`, `eval leak`, `eval redteam`,
    `benchmark run --questions-from` and `data export --kind questions` (`--no-validate` exports
    it as written) exit 2; `evaluate --custom-questions` exits 1, as for any input error there.
    `eval questions refine`, `analyze` and `review`, `eval score checklist` and `eval judge
    agreement` still read it. `benchmark run` exits 2 too, where it ended in a traceback (exit
    5), on a cached `questions/<product>.json` with shared ids, refused by the schema or not
    JSON (a run cut short), saying to fix or delete it (`--force`), and on such a
    `--questions-root` golden set, as `benchmark preflight` already did. No command writes such
    a set any more: `questions refine` keeps it at `<name>.rejected` (exit 2 with `--fill-gaps`,
    before any model call; else 1). A pipeline run gives a generated question whose id the
    custom or earlier set already has a free `q_NNN` (generated ids restart at `q_001` on every
    `--force`, and the merged set held both), and `--fill-gaps` numbers new questions past
    the `gap_<level>_NNN` ids there.
  - A product registry with `products: null` reads its legacy `libraries:` mapping instead of
    loading no products, and logs a warning that it did. A file with both keys logs that
    `libraries` is ignored, and the warning for a value that is not a mapping names the file and
    the key it was read from.
  - A `DOKIMI_OP` marker or `operations.jsonl` line that is valid JSON but not an object (a
    list, a number, `null`), or valid JSON that Python cannot hold (nested too deep, an integer
    over 4300 digits), is recorded as a `log_parse_error` operation instead of crashing the
    harness run. Every `log_parse_error`, unparsable JSON included, says why in
    `metadata.error`. An `operations.jsonl` line that is not UTF-8 is a `log_parse_error` of its
    own; the file's other lines are read, where the whole file was one. A record ends at a
    newline, `\v`, `\f` or `\x1c`-`\x1e`, as before, but no longer at U+2028, U+2029 or U+0085,
    which a JSON string may hold raw: a record with one of them in a string is read whole, where
    it was cut into errors, and two records joined by one of them are one `log_parse_error`. An
    `elapsed_sec` that is no finite number (an integer too large for a float, `1e400`,
    `Infinity`, `NaN`, a string that is not a number) falls through to `duration_sec`, as a
    missing one does, and the duration is 0 when neither is usable.
