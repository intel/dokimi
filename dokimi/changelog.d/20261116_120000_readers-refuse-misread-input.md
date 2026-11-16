### Fixed

- **Three readers no longer misread a malformed input** (#687):
  - A question set whose records share an id (`id`, or `question_id` in a v1 set; `1` and
    `"1"` count as one) is refused, naming the ids, before any answer is paid for: commands
    that take `--questions` exit 2. No command writes such a set any more: `questions refine`
    keeps it at `<name>.rejected` (exit 2 with `--fill-gaps`, before any model call; else 1). A
    pipeline run gives a generated question whose id the custom or earlier set already has a
    free `q_NNN` (generated ids restart at `q_001` on every `--force-regen`, and the merged set
    held both), and `--fill-gaps` numbers new questions past the `gap_<level>_NNN` ids there.
  - A product registry with `products: null` reads its legacy `libraries:` mapping instead of
    loading no products. A file with both keys logs that `libraries` is ignored, and the warning
    for a `products` value that is not a mapping names the file.
  - A `DOKIMI_OP` marker or `operations.jsonl` line that is valid JSON but not an object (a list,
    a number, `null`), or that Python cannot read (nested too deep, an integer over 4300 digits),
    is recorded as a `log_parse_error` operation, as unparsable JSON is, instead of crashing the
    harness run; an `elapsed_sec` too large for a float reads as 0, as a non-number does.
