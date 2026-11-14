<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Agent memory

`dokimi memory` treats an agent's memory store as a subject (tracking issue
[#728](https://github.com/intel/dokimi/issues/728)). The first reader is Claude Code auto memory,
`~/.claude/projects/<project>/memory/`: a `MEMORY.md` index whose first 200 lines or 25KB load at
every session start, and one topic file per memory with YAML frontmatter (`name`, `description`,
`type`: user, feedback, project or reference). Files under `archive/` are read and counted, but not checked and never expected in the index.

## memory lint

```bash
dokimi memory lint ~/.claude/projects/<project>/memory
dokimi memory lint ~/.claude/projects/<project>/memory --json --fail-on warn
```

No model call. Each finding has a rule, a severity, a file and a line:

| rule | severity | what |
|---|---|---|
| `index-dead-link` | fail | the index links a file that does not exist, a directory, or a path outside the memory directory (code spans, fenced blocks and HTML comments hold no link) |
| `index-over-limit` | fail | the index is past the load limit (200 lines or 25KB, as Claude Code loads it) |
| `index-near-limit` | warn | over 80% of the limit |
| `index-missing` | warn | no `MEMORY.md` |
| `index-orphan` | warn | a top-level file the index never links |
| `frontmatter-missing`, `frontmatter-invalid`, `frontmatter-field` | warn | no frontmatter, not YAML, no `name`/`description`/`type` |
| `type-unknown` | warn | a type other than the four |
| `name-duplicate` | warn | two files share a `name` |
| `relative-date` | warn | "today", "next week", "вчера" on a line with no `YYYY-MM-DD` date (code spans skipped) |
| `wikilink-dangling` | note | `[[x]]` matches no file stem or name (`-` and `_` fold) |
| `index-stray-separator` | note | `:,`, `,,` or a trailing `,` in an index line |

Exit 0 when there is no `fail` (no `warn` either with `--fail-on warn`), 1 otherwise, 2 when the path is
not a memory directory.
