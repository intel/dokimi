### Fixed

- **The JSON writers no longer write an integer key beside its string as two equal keys** — `to_jsonl([{1: "a", "1": "b"}], path)` wrote `{"1": "a", "1": "b"}`, which a reader loads as `{"1": "b"}`, so an entry was lost on reload; `True` beside `"true"`, `1.5` beside `"1.5"` and `None` beside `"null"` did the same. `save_artifact` and `to_jsonl` now raise `ValueError` for such a payload and write nothing; the JSON writers that do not go through the shared check (`write_json_atomic`, the report writers, the `commands` writers) are unchanged.
