<!--
A changelog fragment. See changelog.d/README.md.

Uncomment exactly ONE '### <Category>' section below and replace the example
bullet with your entry, written the way it should read in CHANGELOG.md.
Delete the other sections.
-->

### Added

- **Content checks in `catalog check`** — each loaded skill's `SKILL.md` and the text files beside it are checked, with no key and no model call. `legacy-model-id` (warn) names a model ID its vendor has deprecated or retired, with the file, line, status, date and the vendor's replacement; the list is `dokimi/external/legacy_models.toml`, as of 2026-11-07. `hidden-unicode` (warn) names a bidi control, a Unicode tag character or a stray zero-width character, with its line and codepoint. No action is needed; a skill that uses a listed ID or a hidden character now shows a `warn` row until it is fixed.
