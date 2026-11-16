### Fixed

- **Three readers no longer misread a malformed input** (#687):
  - A question set whose records share an id (`id`, or `question_id` in a v1 set; `1` and
    `"1"` count as one) is refused, naming the ids, before any answer is paid for: commands
    that take `--questions` exit 2. `questions refine` still reads such a set to repair it.
  - A product registry with `products: null` reads its legacy `libraries:` mapping instead of
    loading no products. A file with both keys logs that `libraries` is ignored, and the warning
    for a `products` value that is not a mapping names the file.
  - A `DOKIMI_OP` marker or `operations.jsonl` line that is valid JSON but not an object (a list,
    a number, `null`) is recorded as a `log_parse_error` operation, as unparsable JSON is,
    instead of crashing the harness run with `AttributeError`.
