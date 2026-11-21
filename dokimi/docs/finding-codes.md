<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Finding codes

Every finding that `dokimi catalog check` reports has one code. The codes are the
keys of `--ignore CODE`, of `findings[].code` in `--format json` and of the JSONL rows
written by `--per-skill --jsonl` ([row schema](catalog-jsonl.md)). External studies can
count on them: the list is `FINDING_CODES` in `dokimi/external/validate.py`, and a test
(`tests/test_catalog_ignore.py`, `tests/test_catalog_output_docs.py`) fails when a code in a `Finding(...)` call,
or a kebab-case literal or script-check tuple in the content and command checks, is missing from this page, or when
this page lists a code the list lacks. The test does not read other modules, or codes built from variables: those
must be listed by hand.

Stability: a code keeps its meaning. New checks add new codes; a code is not renamed,
split or reused for a different problem. A check that changes its level says so in the
changelog. The `message` text is for people and may be reworded, so do not match on it.

## Levels

- `error`: the skill's state is `error`. The scan fails on a load error or an `unreadable`
  SKILL.md, and on any error under `--strict`.
- `warn`: the skill's state is `warn`. Only `--strict` makes it fail, and only when it grants
  unrestricted Bash and has a risky command (see [catalog check](external-content.md)).
- `note`: information only; it never changes the state.

`--ignore CODE` leaves the findings with that code out of the state and the exit code.
Three codes cannot be ignored (`catalog check` refuses the flag): ignoring them would hide why
a skill does not load, or make the whole-catalog and `--per-skill` exits disagree.
They are `frontmatter-parse`, `not-a-skill-no-frontmatter` and `unreadable`.

## Codes

| Code | Level | Emitted when | Ignorable |
|---|---|---|---|
| `allowed-tools-unknown` | warn | `allowed-tools` names a tool dokimi does not know | yes |
| `allowed-tools-unrestricted-bash` | warn | `allowed-tools` grants unrestricted Bash; with a risky-command code it also fails `--strict` | yes |
| `body-too-large` | warn | the SKILL.md body is over the size limit | yes |
| `bundle-file-unchecked` | note | the search for unreferenced bundled files could not see every name (over 2000 files, over 50 named documents, or a named document that is over 1 MiB, not UTF-8, a symlink or a FIFO), so no count is given | yes |
| `bundle-file-unreferenced` | warn | bundled scripts and documents that neither `SKILL.md` nor a document it names mentions; the message gives the count and three example paths | yes |
| `description-near-duplicate` | warn | the description overlaps another skill's (Jaccard similarity at least 0.5 and at least 3 shared words) | yes |
| `description-no-when` | warn | the description has no "when" clause (the cues are in `when_cues.py`) | yes |
| `description-too-long` | error | the description is over 1024 characters | yes |
| `description-when-not-checked` | note | the description is in a script no when-cue covers, so it is not checked | yes |
| `duplicate` | warn | the SKILL.md is identical to another copy; the copy is counted twice | yes |
| `frontmatter-parse` | error | the frontmatter is not valid YAML | no |
| `frontmatter-unknown-key` | warn | the frontmatter has a key the spec does not define | yes |
| `hidden-unicode` | warn | a bidi control, Unicode tag or stray zero-width character in SKILL.md or a bundled text file | yes |
| `install-unpinned` | warn | a bundled script installs a package without a pinned version | yes |
| `legacy-model-id` | warn | a model ID its vendor has retired or deprecated | yes |
| `link-dead` | warn | `--online`: a URL in SKILL.md answers 404 or 410; a host that does not resolve counts too (not when an environment proxy carries the URL), once any other probe of the run got an answer (a PyPI or npm lookup counts too, including one that answers 404) | yes |
| `link-escapes-skill` | warn | a relative link points to a file outside the skill directory | yes |
| `link-missing` | warn | a relative Markdown link names no file | yes |
| `mirror-drift` | warn | a copy of the same directory name in another path has different SKILL.md text | yes |
| `name-conflict` | warn | the name is also used by a listed skill with different content | yes |
| `name-format` | error | the name is not a valid skill name (characters or length) | yes |
| `name-mismatch` | error | the frontmatter name differs from the directory name | yes |
| `name-missing` | warn | the frontmatter has no name | yes |
| `not-a-skill-no-frontmatter` | error | the SKILL.md is plain Markdown with no frontmatter block | no |
| `not-loaded` | error | a skill reviewed alone did not load | yes |
| `opencode-no-description` | warn | an OpenCode `SKILL.md` (under `.opencode/skills`) has no frontmatter or no description; OpenCode loads it but does not advertise it | yes |
| `package-not-found` | warn | `--online`: a package a command installs is not in its registry | yes |
| `path-escapes-skill` | warn | a `../` code span names a file outside the skill directory | yes |
| `path-missing` | warn | a backticked path under `scripts/`, `references/`, `assets/`, `templates/`, `examples/` or `resources/` names no file | yes |
| `permission-bypass-flag` | warn | a command passes a flag that skips the agent's permission prompts | yes |
| `pipe-to-shell` | warn | a command pipes a download into a shell | yes |
| `script-crlf` | warn | a bundled script has CRLF line endings | yes |
| `script-lint` | note | a bundled script has a lint finding | yes |
| `script-syntax` | warn | a bundled script does not parse | yes |
| `similar-skills-exist` | note | the description overlaps a differently named skill's (Jaccard similarity at least 0.4 and below 0.5, at least 3 shared words); the state stays `ok` | yes |
| `unreadable` | error | SKILL.md exists but the OS will not read it | no |
| `unsupported-skill-format` | note | the skill is under a host directory dokimi holds no spec for (`.kiro`, `.cursor`, ...); the frontmatter and description checks were skipped, an unloaded file was not read | yes |

Codes that other commands report (for example `unsafe-path` in `catalog eval`) are not
in this list; they are documented with their command.
