<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Catalog check output: JSON and JSONL

`dokimi catalog check` writes its skill rows in two forms. The codes in them are listed in
[finding codes](finding-codes.md).

## `--format json`

One JSON object on stdout (`--kind skills`; a `--kind all` scan also has `tasks`):

| Key | Type | Meaning |
|---|---|---|
| `root` | string | the scanned directory |
| `skills` | array of skill rows | one row per declared skill (the row schema below) |
| `tasks` | array | task rows, empty unless `--kind tasks` or `--kind all` |
| `online` | object | only with `--online`: the network checks' summary |
| `listing` | object | `chars`, the total of the rows' `listing_chars` (a mirror with the same name and `content_sha256` counts once; a name whose copies differ is left out); with `--listing-budget` also `budget` and `over_budget` |
| `granularity` | object | only without `--per-skill`: the merge and split hints |

## `--per-skill --jsonl PATH`

`PATH` holds one JSON object per line, one line per skill, written as each skill is reviewed.
The text on stdout is only the counts. A run killed midway keeps the whole lines it wrote, and
`--resume` reads them back (see the `--resume` notes in [external content](external-content.md)).

## Skill row

| Key | Type | Meaning |
|---|---|---|
| `name` | string | the manifest entry's `name` when it sets one. With a manifest and no `name`: the last part of the entry's `catalog_path` (`SKILL.md` when that path names the file) when the skill loads, else `?`; a non-mapping manifest item gives `<entry N>`. Without a manifest: the skill's frontmatter name when it loads, else its directory name |
| `manifest_status` | string or null | the status a `skills.yaml` entry gives the skill; null without a manifest |
| `path` | string or null | the skill's directory as scanned; when a manifest `catalog_path` names a SKILL.md file, that file |
| `relpath` | string or null | the skill's directory relative to the scanned root, with `/` separators |
| `nested_in` | string or null | the relpath of the skill whose directory holds this one; null when it is not nested |
| `state` | `ok`, `warn`, `error` or `skipped` | `error` for a load error or an error finding, `warn` for a warning only, `skipped` for an unloaded skill no format rule read (an unsupported host's plain Markdown), else `ok`; `ignored` codes are left out |
| `error` | string or null | the load error, when the skill did not load |
| `disable_model_invocation` | boolean | the skill sets `disable-model-invocation: true`, so the description-quality checks skip it; the frontmatter length limit still applies |
| `content_sha256` | string or null | digest of the SKILL.md text; null when there is no review, except for a skill charged for a missing `description`, which has the digest of its SKILL.md |
| `skill_sha256` | string or null | digest of every file in the skill's directory (the scan's own output files excluded); null when the directory or a subdirectory cannot be read; used by `--resume` |
| `dokimi_version` | string | the dokimi version that wrote the row; `--resume` redoes rows from another version |
| `listing_chars` | integer or null | characters of the skill's entry in the routing field's listing (`name: description`, then ` - when_to_use` when it is not empty; the description and `when_to_use` count at most 1,536 characters); a skill that fails to load only for a missing or empty `description` is listed by the body's first non-empty line and has that charge, with the `: ` an entry puts after the name (`listing.listing_chars` plus 2), and one whose `description` is only whitespace is charged for that whitespace; null when the skill is not listed (it does not load for another reason, has a spec error other than a long description, or sets `disable-model-invocation`). `--resume` redoes a row without it |
| `listing` | object or null | the entry `repo budget` charges for the skill, listed or not: `description` (the frontmatter one, else the body's first non-empty line), `source` (`frontmatter` or `body`), `description_chars` (description plus ` - when_to_use`, before the cap) and `listing_chars` (name plus the text, capped at 1,536); null when SKILL.md cannot be read or there is no text. `--resume` redoes a row without it |
| `listing_name` | string or null | the name the routing field knows the skill by: the loaded `SKILL.md` name, which can differ from `name` (a manifest alias); null when the skill did not load, except a skill charged for a missing `description`, which has the name it is listed under. The listing total groups copies by it. `--resume` redoes a row without it |
| `duplicate_of` | string or null | the relpath of the identical copy this skill duplicates; null for the original |
| `ignored` | array of strings | the `--ignore` codes in effect, sorted (`[]` when none); `--resume` redoes a row written under other codes |
| `findings` | array of finding objects | the findings left after `--ignore` |

A finding object has three keys: `level` (`note`, `warn` or `error`), `code` (one of the
[finding codes](finding-codes.md)) and `message` (text for people; it may be reworded between
versions, so match on `code`). A `script-crlf` or `script-syntax` finding also has `script`, the
script's path relative to the skill directory.

The field list above is the contract that `tests/test_catalog_output_docs.py` checks against
the rows the scan writes: a key added to a row must be added here.
