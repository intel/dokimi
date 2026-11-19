### Fixed

- **`legacy-model-id` now flags `claude-opus-4-0`, `claude-sonnet-4-0` and `claude-opus-4-1`** — `legacy_models.toml` listed the dated IDs `claude-opus-4-20250514`, `claude-sonnet-4-20250514` and `claude-opus-4-1-20250805` as retired but not their aliases, so a skill that called a retired model by its alias passed `catalog check` with no finding. Each alias now sits beside its dated ID and gets the same retirement date and replacement. (#945)
