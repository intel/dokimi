### Fixed

- **`parse_json(contains=True)` no longer costs starts x text length** — the scan was capped at 1000 `{` or `[` tries, but each try on a long text parsed the whole tail before it failed, so `'[' * 900 + '1,' * 100000` took seconds to read as not JSON. The tries that fail now share a budget of 1,000,000 scanned characters (`CONTAINS_JSON_MAX_SCAN`), and the scan reads the text as not JSON once it is spent, as it does at the start cap. A value found after a few failed tries, and a value of any length when no earlier try failed late, are found as before.
