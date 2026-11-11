### Fixed

- **`contains` JSON checks no longer time out on deeply nested answers** — `parse_json(contains=True)` decoded from every `{` or `[` and copied the rest of the text each time, so an answer of 20000 `[` took seconds on Python 3.13 and hit the 60 s test timeout on 3.14. It now decodes in place, tries at most the first 1000 brackets; an answer whose JSON starts after more brackets than that reads as not JSON.
