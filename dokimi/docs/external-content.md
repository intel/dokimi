---
depends_on:
  - dokimi/dokimi/external/**
  - dokimi/dokimi/commands/catalog.py
  - dokimi/dokimi/commands/catalog_eval.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Pointing Dokimi At An External Content Repository

## Key-free catalog planning and checks

Run the deterministic catalog path against a local checkout:

```bash
dokimi catalog eval /path/to/catalog --tier static --out-dir /path/to/receipts
dokimi catalog eval /path/to/catalog --tier static --out-dir /path/to/receipts --changed-from BASE
```

The dedicated output directory contains `catalog.json` (`catalog_eval.v1`),
`plan.json` (`plan.v1`), `result.json` (`result.v1`) and `report.md`. The plan
declares the always-run catalog integrity check and one static check per selected
skill, with no model. The result accounts for exactly those checks. The installed
checker fingerprint and input bundle/task digests bind the receipts to the bytes
checked (vendored and VCS directories such as `.git`, `node_modules` and `.venv` inside a skill
or task are not part of its digest). A catalog root that is itself a symlink is refused; a
symlink in a parent directory is resolved once, and no link below the root is followed (with no `skills.yaml`, a linked directory beside a discovered skill or under `skills/` is an `unsafe-path` finding, not skipped; links elsewhere, such as `.claude/skills`, are ignored). No model, Docker, skill script, oracle or task verifier runs here.

Tasks are discovered on disk and assigned using their `metadata.skill` field.
`evaluation/harbor/suites.json` is cross-checked: a task present on disk but absent
from a suite remains visible with a warning; a missing declared implemented task,
unknown owner or contradictory ownership fails validation. A task that cannot be
loaded (for example invalid TOML) is a `task-load` finding on its declaring skill, or on
the catalog when no suite declares it. Planned tasks remain
listed separately. Imported skills or suites with `evaluated_here: false` receive
`checks_only`; local skills without actual tasks receive `no_tasks`; other skills
receive `not_measured`. A static pass establishes none of their runtime outcomes,
skill usefulness, discovery rate or hardware performance, and changes no catalog
promotion status.

`--changed-from` compares a locally available base commit with working-tree bytes,
including untracked and ignored files that a skill or task bundle hashes (files
under a vendored directory such as `node_modules` and any `.git` are not hashed, so
they select nothing). Skill resources and owned tasks select their skill, and a
changed or removed task also selects the skill that owned it at the base;
registry, suite, shared machinery and unclaimed changes select conservatively.
A ROOT that git reaches only through an enclosing repository that tracks no file
under it (a skills folder under a git-tracked `$HOME`) is not a checkout: its
`source` has no commit and every skill is selected.
A file git ignores under the shared machinery (`tools/`, `scripts/`, `dokimi/`,
`.github/`) selects nothing and does not mark the source `dirty`, whatever its name.
Selection limits what can fail: a finding in a skill that is not selected is
listed in the report but does not fail the run, while a catalog-wide problem
(manifest, suites, tasks root, a task with no known owner) always does.
Removed skill paths remain visible in the selection receipt. An unavailable base
selects all skills with an explicit note. A docs-only change can select no skills;
the catalog integrity check still runs. Git paths are read as NUL-delimited data,
and candidate hooks, external diff tools and filesystem monitors are not run.

Exit 0 means the deterministic checks passed, exit 1 reports validation findings,
and exit 2 reports unusable input or output. A failed validation rewrites the
receipts with failure; an input error removes earlier success receipts and leaves
an error report when the output directory is usable. The directory must be empty
or contain only this command's earlier receipts, and must be outside the entire
catalog checkout and its ancestors. The directory itself is never renamed or
removed, so it may be a mount point, the current directory or sit under a
read-only parent; receipts are staged in a scratch directory inside it. Linked catalog roots and inputs are refused.
Successful outputs are prepared and schema-checked before replacement.

This path can run in a read-only, key-free catalog workflow. It is not a substitute
for qualified oracle/negative controls and complete paired trials before a scored
skill promotion. The existing `repo-eval.yml` workflow reads repository task
manifests rather than a Harbor skill catalog.

This repository is the measuring instrument. The things it measures —
skills, question sets, executable tasks, exploration tasks, agent profiles —
live in a separate content repository (for example `uxlfoundation/skills`).
Nothing here clones anything: you check the content repo out yourself and name
the directory on the command line.

Every content location is an explicit flag. There is no search path and no
implicit default that points at a sibling checkout:

| Flag | What it names | Commands |
|---|---|---|
| `--root` | a whole content checkout to scan | `catalog check` |
| `--questions-root` | directory holding `<product_key>_golden.json` | `benchmark preflight/run/batch`, `evaluate`, `coverage`, `config_check` |
| `--tasks-root` | directory of executable task dirs (each with `task.toml` or a BenchFlow `task.md`), or of exploration task YAML files | `eval tasks list`/`tasks run`, `eval tasks list --format localization`, `explore run`, `coverage`, `config_check` |
| `--repo-root` | the source tree an exploration task is scored against | `explore run` |
| `--registry` | a `products.yaml` describing products this repo has never heard of | `benchmark preflight/run` |
| `--questions` | one question-set file | `arms run`, `answers generate` |

Skills, profiles and subjects are named by path inside the arm spec itself
(`skill:<path>`, `profile:<path>`, `catalog show --kind subject <descriptor>`), so they need
no root flag.

The transcripts below are real runs against a small demo checkout laid out the
way the readers expect. Substitute your own checkout path.

## Content checks: retired model IDs and hidden characters

`catalog check` also reads the text of each loaded skill: its `SKILL.md` and the text
files beside it (`.md`, `.py`, `.sh`, `.bash`, `.zsh`, `.ps1`, `.json`, `.yaml`, `.toml`, `.html`, `.csv`, `.ini` and similar, up to 1 MB;
hidden folders, `node_modules` and symlinks are skipped). A `SKILL.md` at the checkout root
is checked alone, since its directory is the whole repository. It needs no key and calls no model.
Two checks, both `warn`:

- **`legacy-model-id`**: the text names a model ID its vendor has deprecated or retired. A Cyrillic or Greek look-alike letter or a fullwidth letter counts as its Latin letter.
  The finding gives the file and line, the vendor's status and date, and the replacement
  the vendor names. IDs match whole tokens and case-sensitively: `gpt-4.1` does not match
  `gpt-4`, and `GPT-4` in prose does not match at all. The list is
  `dokimi/external/legacy_models.toml`; its header names the three vendor pages it was
  checked against and the date. Update it by editing that file, with no code change. An entry
  lists only IDs the vendor labels deprecated or retired on the `checked` date; `status` is
  `deprecated` (still served, date ahead) or `retired` (no longer served); `date` and
  `replacement` are the vendor's, verbatim, or empty when it gives none. An entry also lists
  the vendor's alias of a listed ID (`claude-opus-4-0` for `claude-opus-4-20250514`), with
  the same status and date.
  The finding reads every mention of a listed ID as a use of it, so a line that documents a
  retirement (a table of retired models) or shows the old side of a migration is reported too;
  its advice is conditional (`if this text calls the model, use ...`). A catalog that documents
  model history can leave the check out with `--ignore legacy-model-id`.
- **`hidden-unicode`**: a bidi control (U+202A to U+202E, U+2066 to U+2069), a bidi mark (U+200E, U+200F,
  U+061C) with no Hebrew or Arabic letter as the nearest letter on either side in its line, a Unicode tag character (U+E0000 to U+E007F), a stray
  zero-width character (U+200B, U+2060, or a U+FEFF after the start of the file), a soft hyphen (U+00AD), an
  invisible operator or deprecated format character (U+2061 to U+2064, U+206A to U+206F), or a blank
  filler (U+034F, U+180E, U+115F, U+1160, U+3164, U+FFA0, U+2800). A variation selector (U+FE00 to U+FE0F,
  U+E0100 to U+E01EF) is reported at the start of the text, after another selector, or after an ASCII
  character other than a keycap base (`#`, `*`, `0` to `9`), or after a non-ASCII character that is not a symbol
  or an ideograph (a letter, a no-break space); after an emoji, a symbol or an ideograph it is not. U+034F is not reported beside a non-ASCII letter or mark, U+115F and U+1160 beside Hangul (jamo, compatibility and Extended-A/B jamo, syllables,
  halfwidth), and U+2800 beside Braille. Each is reported with its line and codepoint; five
  per file are listed. A zero-width joiner (U+200C, U+200D) is not reported next to an emoji
  or symbol (U+2600 to U+27BF, U+1F000 to U+1FAFF) or in Arabic and Indic script, where it is correct.
  Vendored folders (`site-packages`, `node_modules`, `.venv` and the like) are not scanned, and
  a folder that holds another skill's `SKILL.md` is checked as that skill only.

Limits of the model list: it is a snapshot, so a model a vendor has announced but not yet
labelled deprecated is not listed. Some listed IDs are short names (`o1`, `o3-mini`,
`gpt-4`, `dall-e-2`) that can also appear in prose about a model; a finding there is a
prompt to reword it; a code span does not silence the warning, and there is no suppression yet. A few rows
name no replacement (`sora-2`, `gpt-5.4-cyber`), and the finding then points at the vendor page.
The list includes Imagen and Veo IDs, so it reaches past the text models. This warning does not
fail `--strict`. Of the warnings, only the unrestricted-Bash pair does (`allowed-tools-unrestricted-bash` below).

## The layout the readers expect

```text
<checkout>/
  skills.yaml                        # optional manifest: name, status, catalog_path
  skills/<skill-name>/SKILL.md
  evaluation/harbor/suites.json      # optional manifest: suites -> tasks (status, track)
  evaluation/harbor/tasks/<task>/    # task.toml + instruction.md + tests/ + solution/
  questions/<product_key>_golden.json
  explore_tasks/<task>.yaml
  agent_profiles/<persona>.md
  products.yaml                      # optional: products this repo's registry lacks
```

Both manifests are optional — with no manifest, the readers fall back to a
plain filesystem scan of the same directories (a `SKILL.md` under a `template/`
or `templates/` directory is a scaffold and is left out; vendored and VCS directories such
as `.git`, `node_modules`, `site-packages` and `.venv` are not walked). `skills.yaml` comes in two
shapes, and both hold a *list* of entries:

```yaml
# skills.yaml, uxlfoundation/skills: entries under `skills:`, catalog_path required
skills:
  - name: demo-skill
    status: published
    catalog_path: skills/demo-skill
```

```yaml
# skills.yaml, intel/skills: the file is the list, the skill is at skills/<name>
- name: demo-skill
  maintainer: "github-handle"
```

In the second shape `name` must be a single directory name. An entry without
`status` scans with status `-`; intel/skills reads that as `published`.

```json
// evaluation/harbor/suites.json
{"suites": [{"name": "demo", "tasks": [{"name": "demo-widget", "status": "published", "track": "executable"}]}]}
```

 A checkout that puts tasks somewhere other than
`evaluation/harbor/tasks` can be scanned with `catalog check --tasks-subdir
<relative-path>`; the run commands take the full path as `--tasks-root`
regardless of layout.

## 1. Scan the checkout before you spend anything

`catalog check` is a pre-flight: it reads the manifests, resolves each declared
entry on disk, and reports one line per entry. It never calls a model, so it
costs nothing and is the cheapest way to find out that a suite you were about
to pay to run is half-declared.

```console
$ python cli.py catalog check --root C:/Users/Public/content-repo
# skills (2 declared under C:\Users\Public\content-repo; 1 ok, 0 warn, 1 error)
demo-skill      published       C:\Users\Public\content-repo\skills\demo-skill  ok
not-written-yet planned C:\Users\Public\content-repo\skills\not-written-yet      ERROR: Skill path does not exist: C:\Users\Public\content-repo\skills\not-written-yet

# tasks (3 declared under C:\Users\Public\content-repo)
demo-widget     published       executable      ok
demo-quiz       published       answer-quality  skipped: no task.toml or task.md under C:\Users\Public\content-repo\evaluation\harbor\tasks\demo-quiz (manifest status='published')
demo-later      planned executable      skipped: no task.toml or task.md under C:\Users\Public\content-repo\evaluation\harbor\tasks\demo-later (manifest status='planned')
$ echo $?
1
```

Read that output as three outcomes, not two:

- `ok` — the entry loaded and can be run.
- `skipped` — a declared entry the scan is *expected* to pass over: a
  `planned` task with no directory yet, or a `track` that `tasks run` does not
  drive (`answer-quality` entries are question-set material, not Docker tasks).
  For a skill, it is an unloaded `SKILL.md` that no format rule read (see
  [Skill formats](#skill-formats)); `--ignore` does not make it an `ok`.
- `ERROR` — an entry that should have worked and did not.

Only load errors change the exit code, so `catalog check` exiting 0 means
"everything declared as runnable is runnable". A spec error (below) changes it
only under `--strict`. A root with no `SKILL.md` at all (and no skills.yaml entry)
prints `no SKILL.md found under <root>` to stderr and exits 1. With `--kind all`, a
tasks-only root only warns, and exits 1 under `--strict`. Note the asymmetry above: a `planned` *task*
with no directory is skipped, while a `planned` *skill* with no directory is an
error — the skill reader has no notion of a not-yet-written entry. Either drop
such a skill from `skills.yaml` or expect the non-zero exit.

A loaded skill is also checked against the Agent Skills spec. Its `name` must be its directory name
and kebab-case (at most 64 characters; a `SKILL.md` directly in the scanned root is not compared
with the root directory's name), and its `description` at most 1024 characters: a violation is an
`error`, printed under the skill. It exits 1 only with `--strict`; without it, the scan reports the
error and still exits 0. A `name` missing from the frontmatter, or a `SKILL.md` byte-identical to
another skill's, is a `warn`. So is a skill whose `name` another skill shares with different content
(`name-conflict`), when both are listed: neither is `disable-model-invocation` or has a spec error
(a description over 1024 characters, as the only error, does not unlist a skill for routing).
The finding names the other path, and `dokimi skill routing --against` (or `catalog routing ROOT`) skips that name rather than
the whole catalog. Of identical copies, the one nearest the root (fewest path parts, ties to path
order) is the original, the copy the `skill-catalog:` arm offers; each other copy is the `warn` and
names it. Warnings never change the exit code. `disable-model-invocation: true` is printed as a
`note`, because the model is never offered that skill.

A `SKILL.md` with no frontmatter (plain Markdown, often a README under the
wrong name) does not load. Its finding, `not-a-skill-no-frontmatter`, holds a
frontmatter block to start from. The description is the paragraph after the
file's H1 (the first paragraph when there is no H1). The name is the
directory's below the scanned root, where the loader checks it against the
directory; at the root, or when the directory name is not a valid name, it is
the H1 in kebab-case (ASCII letters and digits, at most 64 characters), or the
directory name when the H1 has none, or `my-skill` when neither has one. The summary line counts these apart from the other errors
(`3 error, of them 2 plain Markdown with no frontmatter`).
A `---` block with a `name:` or `description:` line in the first 20 lines, or that opens with a YAML key
(any, `title:` included), keeps the loader's error and gets no hint. So does a block of blank lines between two `---` lines.
A block that opens with a `[` or `{` that is not a Markdown link also keeps the loader's error. A `---` rule, or a block with no YAML key and no such opener, is plain Markdown and gets this finding.

### Skill formats

The catalog check reads the host from the path below the scanned root (`skill_format` in
`dokimi/external/skill_format.py`). The Agent Skills spec applies to `.claude`, `.codex`, `.gemini`,
`.github` and `.agents` skill directories, to a `skills/` directory with no host, and to any hidden
directory that is not a listed host. Two cases have their own rules:

- **OpenCode** (`.opencode/skills/`, and the singular `.opencode/skill/`, which the OpenCode docs do
  not list): frontmatter is optional and the ID comes from the path, so a `SKILL.md` with no frontmatter
  is not an error. It gets one `warn`, `opencode-no-description`, because OpenCode does not advertise a
  skill without a description, and dokimi reads only a skill with one. A name that is not kebab-case is
  a `warn` (`name-format`), and a name that differs from the directory or a description over 1024
  characters is not a finding. Source: https://opencode.ai/docs/skills/
- **A skills directory under a listed host dokimi holds no spec for** (`UNSUPPORTED_HOST_DIRS` in
  `dokimi/external/skill_format.py`: `.cursor`, `.kiro`, `.windsurf`, `.trae`, and the others the corpus
  holds): the frontmatter and description checks do not run, and one `note`, `unsupported-skill-format`,
  says so. A loaded skill gets the safety and content checks and keeps its `ok` with that note; an unloaded
  one is `skipped`, with the note, unless a command check warns. Any other hidden directory, such as `~/.config` above a `skills/` root,
  is not a host, and its skills get the spec checks.

Under either rule, a `SKILL.md` that did not load still gets the command checks (`pipe-to-shell` and the
others) and `allowed-tools-unrestricted-bash`, so `--strict` fails a Bash grant with a risky command there
as it does for a loaded skill.

The scan also checks the bundle and the description. A relative Markdown link
to a file the skill does not have is a `warn` (`link-missing`), and says when the
file exists from the catalog root instead. Example links such as `[Title](URL)`
are common, so `--strict` does not fail on them. A code span that names a file
under a bundle folder (`scripts/`, `references/`, `assets/`, `templates/`,
`examples/`, `resources/`), alone or inside a command such as
`python scripts/run.py`, is a `warn` (`path-missing`) when the skill does not
have that file. A code span that is the label of an inline link to a URL (`[`examples/a.py`](https://...)`) or of a full reference to one (`[`examples/a.py`][ref]`) is the link's text and is not checked; one in a shortcut or collapsed reference (`[`examples/a.py`]`, `[`examples/a.py`][]`) is still checked.
Folders, flat names, placeholders (`scripts/<name>.py`),
user-local `*.local.md` files (`references/secrets.local.md`, which the user keeps out of git)
and repo-root paths such as `src/app.py` are not checked, and
neither are code fences. A file name with a space is read only when it is quoted or its space is escaped
(`'scripts/my run.py'`, `scripts/my\ run.py`). Only `/` separators are read: a backslash path such as `scripts\run.py` is not checked. Paths joined by `,` or `;` with no space are read one by one when the next one is under a bundle folder (`scripts/a.py,scripts/b.py`); other commas, and an escaped `\,`, stay in the name. Some hits are files the skill tells the agent to
create; `--ignore path-missing` leaves the code out. A link, or a `../` code span,
that reaches a file outside the skill directory is a `warn`
(`link-escapes-skill`, `path-escapes-skill`), since it breaks when the skill is
installed alone. An `allowed-tools` that grants `Bash` for every command (`Bash`
with no pattern, or `Bash(*)`) is a `warn` (`allowed-tools-unrestricted-bash`);
its message names a `pipe-to-shell` or `permission-bypass-flag` hit in the same
skill, since those commands then run with no prompt, and that pair fails `--strict`. A description with no "when" clause (`use when ...`, `when deploying ...`) is a `warn`
(`description-no-when`), and so is one that overlaps another skill's by at least
half and shares three words (`description-near-duplicate`). Under a different name, an overlap from 40% up to that line that shares at least three words is a `note` (`similar-skills-exist`) that leaves the state `ok`. The `description-no-when` cue is read in English,
Spanish, French, German, Portuguese, Russian, Chinese, Japanese and Korean, and a description in a script none
of them covers (Arabic, Thai, Hebrew, …) is not checked: it gets a `note` (`description-when-not-checked`), so not checked does not read as a pass: its row says `note:` instead of `ok`, and the counts line `1 ok (1 not checked)`. A Latin-script language without a list of its own
(Polish, Dutch, Turkish, Vietnamese) is checked against these cues. A word that only states a capability is not a cue:
Japanese いつ counts only before a use verb, Chinese 耗时, 超时 and 延时 do not count directly before 调用 or 执行 (a comma or 请 between them restores it), and German `beim`
counts only in the same sentence as a use verb (verwenden, nutzen, ...). Two skills in different paths with the same
directory name and description but different `SKILL.md` text are a `warn` on both
(`mirror-drift`), naming the other copies. A bundled script or document (`.py`, `.sh`, `.js`,
`.ts`, `.md`, `.txt` and similar) that neither `SKILL.md` nor a document it names ever names, by
path, file name or folder, is counted in one `warn` per skill (`bundle-file-unreferenced`): the agent
finds it only by browsing. Only a link to the skill root in `SKILL.md` (`./`, or `${CLAUDE_SKILL_DIR}/`) names every file; a link to the root from any other document names only its target. Data, images, licence, readme, changelog and `AGENTS.md`-style files, a root translation of `SKILL.md` (`SKILL.ru.md`, `SKILL.zh-CN.md`),
`_`-prefixed helpers, tests and `evals/` folders, hidden and vendored folders, and nested skills are
not counted, nor is a skill at the root of the scanned checkout. The check then gives no count and no `warn`; it gives a
`note` (`bundle-file-unchecked`) that says why, when the search cannot see every name: a bundle over 2000 files, more than 50 named
documents to read, a named document over 1 MiB (truncated: the note names the file, and its names are not read), not UTF-8, a symlink or a FIFO, a folder that cannot be listed. Skills with
`disable-model-invocation: true` are exempt from the description checks.

The scan also reads the scripts a skill bundles: each `.sh`, `.bash` and `.py` file, and each file that
starts with `#!`, under the skill directory (a file over 1 MB, and a link out of the directory, is not read).
At most 200 files of a bundle are walked: every file counts, `SKILL.md` and non-script files included, in walk
order (a directory's files sorted by name, then its subdirectories). Files past the cap are not checked and give
no finding; a subdirectory with its own `SKILL.md` is another skill and is skipped.
A file with a `#!` line and no `.sh`, `.bash` or `.py` suffix is read for CRLF only.
Nothing is run or imported. In `catalog check --json` and `catalog eval --json` a `script-crlf` or `script-syntax` finding carries `script`, the
script's path relative to the skill directory, while `file` stays the skill's `SKILL.md`. A CRLF line ending in a shell script or a `#!` file is a `warn` (`script-crlf`): bash and a
shebang interpreter fail on the carriage return on Linux. A Python file that the running Python's
`ast.parse` rejects is a `warn` (`script-syntax`). So is a shell script that `bash -n` rejects: the text
goes to bash on stdin with no environment, and only when bash is on PATH; a script whose shebang names
a shell other than bash, sh, dash or ksh is not checked that way, and a `.sh` with no shebang is taken as bash. The shebang's `env` options and `NAME=value` words are looked through, and `-O extglob` is given to `bash -n` when the shebang asks for it, when the shebang names ksh (which always has the extended patterns on), or when a `shopt -s` command of the script turns extglob on (`bash -n` runs no `shopt -s extglob`). A `shopt -s extglob` counts wherever it is: in a function, a `( )`, an `if` branch, or later in the file than the pattern it would parse. The option is then given for the whole script, which is the rule on purpose: it reads a script right whose top-level `if` branch sets extglob and is followed by extglob patterns. The cost is a missed error: a pattern that is an error without a shopt, but is accepted because a shopt sits elsewhere (in a function, a subshell, an untaken branch, or later in the file), is not flagged. A shebang argument that changes parsing and is not one of these leaves the script unchecked. When `ruff` or `shellcheck` is on
PATH, its `.py` files are linted with `ruff check --select S,E9,F63,F7,F82` and its `.sh` and `.bash`
files with `shellcheck -f json`, and one `note` (`script-lint`) gives the count and the top three rules of each tool. Both run with their config
ignored (`--isolated`, `--norc`), so a skill's own config files do not change the result. A missing tool
adds nothing, and so does a tool that cannot start. The vendored and scaffold trees are left out
(`.git`, `node_modules`, `site-packages`, `.venv`, `templates` and the rest the loader skips). A skill
at the root of the checkout has no script bundle: its bundle would be the whole checkout, tests included.

The scan also reads the shell commands in `SKILL.md` and its bundled text files; nothing is run. A `curl`
or `wget` piped into a shell or an interpreter (`| sh`, `| sudo bash`, `| python3`) is a `warn`
(`pipe-to-shell`); an inline `python -c`, `node -e` or `ruby -e` program, or `python -m`, takes the download as data and is not, unless the program execs or evals. So is `--dangerously-skip-permissions`, `--yolo` or `bypassPermissions`, which switch off
an agent's permission prompts (`permission-bypass-flag`). A `pip`, `npm`, `pnpm` (`add`, `install`, `i`, `dlx`), `pnpx` or `npx` install that names a
package with no version is a `warn` with the count of distinct packages for the skill (`install-unpinned`); the
same install shown twice is one, and a command inside the quoted text of an `echo` or `printf` is not an install (unless a `$(...)` or backtick substitution in it runs it, or the output is piped to a shell); a requirements
file, a local path, a URL or a `git+` source is not counted. These checks read a Cyrillic or Greek look-alike letter, or a fullwidth letter, as its Latin letter, so `сurl | bash` with a Cyrillic `с` is still a `pipe-to-shell`; the letter list is short, and a look-alike outside it escapes. Installs are read from code only: the fenced
blocks and code spans of a prose file (Markdown, `.mdx`, `.txt`, `.rst`, `.adoc`), and the whole of any other
text file, such as a script. The scan reads at most 200
bundled files of 256 KiB or less; a binary or non-UTF-8 file counts toward the 200 too and is not checked. A skill at the root of a
checkout reads its own `SKILL.md` only.

Frontmatter that is not valid YAML (a bare colon in `description:`, say) stops
the skill from loading. The scan reports that as a `frontmatter-parse` error
carrying the YAML message, and it exits 1 with or without `--strict`, as a load
error does. The YAML message is on the indented `frontmatter-parse` line under
the skill (and in its `findings` in JSON); the row's main line and its JSON
`error` field say the frontmatter has invalid YAML, with the line, not that the
skill is missing a `description`.

A `SKILL.md` that loads but cannot be read during the scan (a permission error, or
a name the OS refuses) is an `unreadable` error. It exits 1 with or without
`--strict`, as `frontmatter-parse` does, and routing leaves that skill out, since
its disable flag and digest are unknown.

A `SKILL.md` with no frontmatter at all (it does not start with a `---` line) also
stops the skill from loading, and its error says "no frontmatter". One whose
`---` block is opened and never closed says "unclosed frontmatter". Neither says
"missing a `description`", which is reserved for a closed block without one.

The scan also checks the size and the vocabulary of a loaded skill. A frontmatter
key that neither the Agent Skills spec nor Claude Code names (a typo such as
`colour:`) gets a `frontmatter-unknown-key` note because another harness may read it.
`allowed_tools` gets a warning under the same code and suggests `allowed-tools`,
the key Claude Code reads for tool grants. An `allowed-tools` entry that is
not a Claude Code tool is a `warn` (`allowed-tools-unknown`); `mcp__*` tools are not
checked, and a `Tool(pattern)` entry is checked by its tool name. A body over 500
lines, or about 5,000 tokens (characters / 4, no tokenizer), is a `warn`
(`body-too-large`): move the detail into bundled files. A name over 64 characters is
the `name-format` error, and its message says the length. These are warnings, so
they do not change the exit code.

`--strict` makes `catalog check` a gate on the spec as well as on loading. The
text output and `--format json` (one object: `state`, `findings`,
`content_sha256` of the `SKILL.md`, `skill_sha256` of the skill directory,
`duplicate_of`, for a script or a CI gate) are the same either way.

The `rates` object reports the share of skills in each state and the share with each finding code.
Each skill belongs to the nearest directory containing a `.git` entry between its directory and the
scanned root, or to the scanned root if none has one. `rate` weights each skill equally. `repo_rate`
weights each repository equally by averaging its within-repository share. An empty scan reports
`null` for both shares. An `ok` row with an unchecked description or format contributes to
`not_checked`. A row with state `skipped` contributes to `skipped`. With two or more repositories,
the text output includes both shares for each state and up to five codes, ranked by the larger share.

After the skill rows, `catalog check` prints a `# granularity` section: skill pairs
that look like merge candidates (similar descriptions, shared trigger clauses, overlapping
bodies, duplicated sections or code blocks) and skills that look like split candidates
(two of: a body past 12,000 characters, three or more `when` clauses with no word in
common, two or more section topics that share little vocabulary and do not reference each
other). It reads the skills a harness would list: a skill with `disable-model-invocation`,
a spec error other than an over-long description, or an identical copy of another skill
is left out, and two skills with the same directory name are shown by their path. It is
free, runs no model, and is hints only: it never changes the exit code, and the JSON
carries it under `granularity` with `hints_only: true` and no verdict. Its `next:` line
names a planned, paid follow-up command (a model run over the candidates), which is not
yet available; its flags may change before it lands. The thresholds are conservative and
the section is a shortlist, not a judgement.

### Network checks (`--online`, opt in)

`catalog check` opens no connection by default. `--online` adds two checks that
do, and only these two; each finding is a `warn`, so the exit code does not change:

- `package-not-found`: a `pip install` (or `pip3`), `npm install` or `npx` name in
  a skill that PyPI (`https://pypi.org/simple/<name>/`) or the npm registry answers
  404 for. Flags, local paths, `git+`, URL and `file:` specs, and placeholders such as
  `<package>` or `your-package` are not names.
- `link-dead`: an `http(s)` URL in `SKILL.md` that answers 404 or 410, or whose host
  does not resolve while another request in the run got an HTTP answer (with none, the
  link is not checked: the resolver is not shown to work). A HEAD request is tried first and a GET only when HEAD is not 2xx or 3xx.
  Placeholder hosts (`example.com`/`.org`/`.net`, `localhost`, `.test`, `.invalid`,
  a host or a path segment starting `your-*`, as in `github.com/your-username/your-repo`,
  a path segment `OWNER`, `REPO`, `USER` or `USERNAME`), hosts of the operator's network
  (loopback, private, link-local, CGNAT, reserved and multicast addresses, a name with no
  dot such as `my-api:8000`, `.local` and `.internal` names) and template URLs (`<id>`,
  `{id}`, `${VAR}`) are not requested. The host is read as written, not resolved: a public
  name that points at a private address is still requested. A URL in a code span or a
  fenced block is an example, not a link, and is not requested either; a `#fragment` is not
  part of the request, so anchors of one page are one request. At most 20 URLs per skill are
  checked.

A 403, 429 or 5xx, a timeout, a refused connection or any other network error is not a
finding: the registry or site may refuse or be unreachable, not be gone. A target not checked
(a network error, a DNS failure with no answer in the run, a redirect into a non-public host,
or a URL past the 20-per-skill cap) gets no finding: it is counted in one summary line,
`# online: N checked, M not checked (network error: a, ...)`, and under `online` in the JSON
report. Each distinct
package or URL is requested once per run, with at most 8 requests in flight and a 5-second
timeout per request. `--online` does not combine with `--per-skill` (its workers would
each hold their own cache, and `--resume` would keep rows no network check made), and
it applies to skills only.
The package scan reads one line at a time: a requirement in quotes or on a `\` continuation
line is not checked, and a word after an install command on the same line is read as a package name unless it is
a common English word that ends the list (such as `in`, `for` or `to`).

```console
$ python cli.py catalog check --root C:/Users/Public/content-repo --online
```

Restrict the scan with `--kind`:

```console
$ python cli.py catalog check --root C:/Users/Public/content-repo --kind tasks
# tasks (3 declared under C:\Users\Public\content-repo)
demo-widget     published       executable      ok
demo-quiz       published       answer-quality  skipped: no task.toml or task.md under C:\Users\Public\content-repo\evaluation\harbor\tasks\demo-quiz (manifest status='published')
demo-later      planned executable      skipped: no task.toml or task.md under C:\Users\Public\content-repo\evaluation\harbor\tasks\demo-later (manifest status='planned')
$ echo $?
0
```

A large catalog is easier to scan one skill at a time:

- `--per-skill` reviews each skill alone. The five cross-catalog checks
  (`duplicate`, `name-conflict`, `mirror-drift`, `description-near-duplicate`,
  `similar-skills-exist`)
  compare a skill with the others, so they do not run in this mode; every other
  check does. The granularity hints (merge and split candidates) need the whole
  catalog too, so they are not run here; the text output says so in a
  `# granularity: not run with --per-skill` line; `--format json` has no
  `granularity` key in this mode.
- `--listing-budget CHARS` adds up each listed skill's entry (`name: description`, then
  ` - when_to_use` when the frontmatter sets it and is not empty; characters, not bytes, and no line break between
  entries; the description and `when_to_use` count at most 1,536 characters per entry, as Claude Code cuts them; a byte-identical `SKILL.md` under another path is one entry, as the routing field keeps one card
  for it, while a name whose copies differ is left out, as there) and compares it with CHARS. The total is `listing.chars` in the JSON report, with
  `budget` and `over_budget`. Over the cap is a `listing-over-budget` warning after the counts,
  and exit 1 with `--strict`; a total equal to CHARS passes. The whole-catalog scan, `--per-skill`,
  `--concurrency` and `--resume` count the same way: a resumed row keeps its `listing_chars`, and the cap is
  checked against this run's value, so lowering it can fail a resumed `--strict` scan whose receipt is
  still valid. `--ignore listing-over-budget` drops the warning, the exit and the line that carries the total
  (the report keeps it); it is not in a row's `ignored`, so adding or dropping it does not redo a resumed row.
- A `SKILL.md` inside another skill's directory is marked with `nested_in`
  (the enclosing skill's path) in JSON and with a `note` in text. It is counted
  by default; `--skip-nested` leaves it out of the scan.
- `--ignore CODE` (repeatable) leaves the findings with that code out of the scan:
  they are not printed, and they do not set the state or the `--strict` exit. The
  codes are the `code` of each finding in the JSON output (`name-mismatch`,
  `description-no-when`, `description-when-not-checked`, ...); an unknown code is a usage error. `frontmatter-parse`
  and `not-a-skill-no-frontmatter` are refused (a skill that does not load always
  fails), and so is `unreadable` (a
  `SKILL.md` the OS will not read fails the scan too), and `--kind tasks`.
- `--fail-on-code CODE` (repeatable) exits 1 when a finding with that code is left after
  `--ignore`, whatever its level: a `note` can fail the scan while its skill's state
  stays `ok`. `--ignore` filters first, so a code in both is not checked. An unknown
  code is a usage error, and so is `--kind tasks`, and so is a code this scan never reports:
  `duplicate`, `name-conflict`, `mirror-drift` and `description-near-duplicate` with `--per-skill`,
  `link-dead` and `package-not-found` without `--online`, `upstream-stale`,
  `license-dropped-from-upstream` and `risk-inherited-from-upstream` without `--upstream` or
  `--upstream-manifest`. `--strict` keeps its meaning. With
  `--json` the skill's subject has verdict `fail`. `--resume` applies the current
  `--fail-on-code` to the rows it keeps, so changing it needs no rescan.
- `--jsonl PATH` writes one JSON row per skill as it is reviewed and prints only
  the counts. `--concurrency N` reviews N skills in parallel. `--resume` keeps the rows
  already in PATH and reviews only the skills without one (a half-written last
  line from a killed run is dropped). Each row carries `skill_sha256`, the
  digest of every file in the skill's directory, and `ignored`, the sorted
  `--ignore` codes in effect (`[]` when none), and `dokimi_version`, the version
  that wrote it. `--resume` keeps a row only while that is unchanged and the
  `--ignore` codes are the same: a skill whose `SKILL.md` or bundled file changed,
  a row written under other codes or by another dokimi version, or a row without
  `skill_sha256` is reviewed again. Without `--resume`, PATH is replaced.
  Every `SKILL.md` is loaded before the first row, so memory still grows with
  the catalog. A `--concurrency` worker that dies ends the scan with exit 1.

The finding codes are listed in [finding codes](finding-codes.md), and the fields of the
JSON report and of each JSONL row in [catalog check output](catalog-jsonl.md).

`--jsonl`, `--concurrency` and `--resume` need `--per-skill`, and `--jsonl` does not
combine with `--format json`; any other misuse exits 2. From Python,
`dokimi.external.review_skill_dir(path)` returns the `SkillReview` of the one
skill in `path`.

### Provenance: which upstream a copy came from (`catalog provenance`)

A skill copied from another repository drifts from it: the upstream is updated and the
copy is not, or the copy is edited and drops the licence. `catalog provenance` matches each
skill under `--root` to a version of an upstream you name, offline. Dokimi ships no
upstream list: give it the upstreams yourself. When sources tie, checkouts come before manifests.
Within each type, the first source listed wins.

- `--upstream CHECKOUT` (repeatable) is the top level of a local git checkout; a folder inside another repository is refused. For a blobless clone, fetch the skill history before running Dokimi.
  A full clone reads the history reachable from `HEAD`. Other branch and tag tips are not scanned.
  On linear history the commit count is exact. With parallel branches,
  the count can include a change on a sibling branch. A shallow clone sees only its own history.
- `--upstream-manifest FILE` (repeatable) is a JSON file of one upstream, for an upstream that
  is not a git checkout. Its `schema` field is the version-1 upstream schema string shown here:

  ```json
  {"schema": "dokimi.upstream.v1", "name": "acme-skills",
   "head": {"commit": "2c5958e", "date": "2026-04-01T00:00:00+00:00"},
   "skills": [{"path": "skills/pdf/SKILL.md", "content": "---\nname: pdf\n...", "head": true,
               "commit": "9f1c2ab", "date": "2026-03-01T00:00:00+00:00"}]}
  ```

  `content` is the text of the file, hashed as UTF-8; line endings count, except that a CRLF copy also matches the LF form of its text (as `git hash-object` reads it under `core.autocrlf`), against a manifest or a checkout. `head`, `commit` and `date` are optional. The git blob
  sha is computed from `content`, so a manifest and a checkout agree.

```text
$ dokimi catalog provenance --root ./my-plugin --upstream ~/src/acme-skills
# upstream acme-skills: head 446e0e560cff 2026-04-01, history full
# skills (2 under ./my-plugin: 1 exact, 1 similar, 0 unmatched; 1 edited; 0 at head; median 3.0 months behind)
skill	match	upstream	version	behind	edited
skills/pdf-copy	exact	acme-skills:pdf/SKILL.md	88fa89b3fcc7 2026-01-01	1 commits, 3.0 mo	no
skills/pdf-edit	similar	acme-skills:pdf/SKILL.md	88fa89b3fcc7 2026-01-01	1 commits, 3.0 mo	yes
```

A copy is matched by its bytes first: an identical `SKILL.md` at the upstream head or in its
history is `exact` (not edited), and the newest such version is the one reported. Otherwise
an upstream version with the same frontmatter `name` and a word Jaccard similarity of at least
0.8 is `similar` (edited). Anything else is `unmatched`. `behind` counts the commits that
touched the file after the matched version, and the months run from that version's commit date
to the upstream head. When the matched path is no longer in the upstream head tree (the skill was
renamed or deleted), the row says `removed_at_head` and the text adds `path removed at head`: `behind`
then counts only the commits that touched the old path. A copy that does not load (no `description`,
broken YAML) is matched by its bytes like the others, and the copy's frontmatter `name`, not its
directory name, is the name for a `similar` match. A checkout that lacks blobs of its history (a
partial clone) reports `history: partial` with `missing_blobs`; fetch them for an exact count.
When a manifest leaves the head of the matched path unspecified, the behind column reads
`unknown`. Its JSON fields `at_head`, `behind_commits`, and `behind_months` are null. `--format json` adds the upstreams, one row per skill and a `summary`.

### Upstream findings in `catalog check` (`--upstream`, `--upstream-manifest`)

Give `catalog check` the same upstreams as `catalog provenance` and each loaded skill that matches one gets
up to three more findings. Nothing is fetched, and none of them is an error:

- `upstream-stale` (note): the copy matches an older version of a path whose head is known. For manifests,
  an entry of the same path must carry `"head": true`. The top-level `head` describes the repository. A note
  preserves the state and exit code. The message says how many commits and months behind the head it is, where the
  history gives them.
- `license-dropped-from-upstream` (warn): the copy is edited, its upstream version declares a `license`, and the copy
  declares none. An edited copy that changes the license to another value is not reported. An unedited copy has the
  upstream bytes, so it cannot drop one.
- `risk-inherited-from-upstream` (warn): the copy keeps a risk its upstream version has too. The risks are the ones the
  scan already reports: unrestricted Bash (`allowed-tools-unrestricted-bash`), `pipe-to-shell` and
  `permission-bypass-flag`. The copy's side is its own findings, bundled files included. The upstream side is that
  version's `SKILL.md` alone, since an upstream version is one file, not a folder.

The warnings follow the usual rules: they do not fail `--strict`, and `--ignore` takes each code. The flags do not
combine with `--per-skill` (the upstream index is built once, in one process) or with `--kind tasks`.


## 2. The awareness half: question sets

A question set is `<questions-root>/<product_key>_golden.json`, in the format
described in [methodology.md](methodology.md#question-record-format): new sets
put each question's text under `question` (`questions.v2`); existing sets that
use `text` (`questions.v1`) are still read, as are older hand-written shapes
(no `id`, `question_id`/`question_text`, a list `metadata`), with a one-line
notice. The golden set is only ever read; the run writes its own v2 copy. Point
`--questions-root` at the checkout's `questions/` directory and the pipeline
picks up the golden set for the product it is running:

```console
$ python -m dokimi.coverage \
    --tasks-root C:/Users/Public/content-repo/evaluation/harbor/tasks \
    --questions-root C:/Users/Public/content-repo/questions
| Product | Awareness (questions) | Work (tasks) | Status |
| --- | --- | --- | --- |
...
| `onetbb` | yes | 1 | covered |
```

(One row per registered product — 24 of them; only the row this checkout
covers is shown.) `onetbb` is `covered` because the checkout has both
`questions/onetbb_golden.json` and one task tagged `onetbb`.

Resolution is logged, including the miss:

```text
INFO Using golden questions: C:\Users\Public\content-repo\questions\onetbb_golden.json
WARNING questions root C:\Users\Public\content-repo\questions has no golden set for onemkl; running without a golden question set
```

`benchmark run` logs a warning and continues with generated questions only.
`benchmark preflight` refuses a missing golden set and exits 2.
If you are comparing arms, that is usually not what you want; check the coverage matrix first.

`benchmark preflight` resolves the whole plan without calling a model, and
echoes the root it will read:

```console
$ python cli.py benchmark preflight --library demo \
    --registry C:/Users/Public/content-repo/products.yaml \
    --questions-root C:/Users/Public/content-repo/questions
Resolved benchmark plan
  target: demo
  name: demo
  description: Worked example product for the external-content walkthrough.
  doc_source: context7
  output_dir: results/demo_final
  answer: openai/gpt-6.1-sol
  judge: anthropic/claude-sonnet-5-5
  questions_root: C:/Users/Public/content-repo/questions
  retrieval: semantic-only, top_k=3
  arms: without_docs vs with_docs
  max_tokens_per_question: 4000
  concurrency: 5
  context7_id: demo-org/demo
  golden questions: C:\Users\Public\content-repo\questions\demo_golden.json (2 items)
```

(It exited 2 here because no API keys were set: `✗ missing OPENAI_API_KEY for
provider openai`. Key checks are part of the pre-flight.)

For a direct N-arm comparison, hand `arms run` the question file itself:

```bash
python cli.py arms run --product demo \
  --questions C:/Users/Public/content-repo/questions/demo_golden.json \
  --arm baseline,skill:C:/Users/Public/content-repo/skills/demo-skill \
  --judge --judge-model <a-different-model>
```

This is the one command on this page that bills a provider, so no transcript is
shown; [evaluating-treatments.md](evaluating-treatments.md) covers reading its
report. Section 5 shows how to check the arm specs resolve first.

## 3. The work half: executable tasks

`--tasks-root` is the directory that *contains* task directories:

```console
$ python cli.py eval tasks list --tasks-root C:/Users/Public/content-repo/evaluation/harbor/tasks
demo-widget     easy    demo,widget
onetbb-parallel-reduce  medium  onetbb
```

The third column is `[metadata] tags`; those tags are what tie a task to a
product in the coverage matrix (a tag matches a product's key in
`products.yaml`, or one of that product's `tag_aliases:`).

A with-skill treatment arm over an external skill is two harnesses over one
task set — `--dry-run` validates the wiring without Docker or a model:

```console
$ python cli.py tasks run --tasks demo-widget \
    --tasks-root C:/Users/Public/content-repo/evaluation/harbor/tasks \
    --harness docker-oracle,docker-claude-skill:C:/Users/Public/content-repo/skills/demo-skill \
    --baseline-harness docker-oracle \
    --model us.anthropic.claude-haiku-4-5-20251001-v1:0 \
    --dry-run --out results/demo-widget.json
Saved task run artifact: results\demo-widget.json
Saved task run report:   results\demo-widget.md
docker-oracle: 0/1 pass (0.00%), ops=1, elapsed=0.00s
docker-claude-skill:C:/Users/Public/content-repo/skills/demo-skill: 0/1 pass (0.00%), ops=1, elapsed=0.00s
docker-claude-skill:C:/Users/Public/content-repo/skills/demo-skill vs docker-oracle: pass-rate delta +0.00%
```

A dry run reports every cell as not passed; only the matrix shape and the
artifact are being checked. See
[coding-harnesses.md](coding-harnesses.md) for the harness list, the real
Docker flow, and the paired statistics on this track.

## 4. Exploration tasks: `--tasks-root` plus `--repo-root`

Exploration tasks are YAML files that ask where something is, and are scored
against a source tree. The two roots are separate because the question and the
code being searched normally live in different checkouts — `--tasks-root` is
the YAML directory, `--repo-root` the tree a task is scored against when its
own YAML omits `repo_root:`.

```console
$ python cli.py eval tasks list --format localization --tasks-root C:/Users/Public/content-repo/explore_tasks
demo-locate-widget      demo    1 ref(s)        Where is the widget width clamped?

$ python cli.py explore run --all \
    --tasks-root C:/Users/Public/content-repo/explore_tasks \
    --repo-root C:/Users/Public/content-repo/product-checkout \
    --arm oracle,empty --baseline-arm empty --out results/explore.json
Saved explore run artifact: results\explore.json
oracle: n=1 file_f1=1.000 line_f1=n/a validity=1.000
empty: n=1 file_f1=0.000 line_f1=n/a validity=1.000
oracle vs empty: file_f1 Δ +1.000
```

The `oracle` and `empty` arms are the ceiling and floor: oracle cites the
task's own references (F1 1.000), empty cites nothing (0.000). Getting those
two numbers is how you confirm `--repo-root` points at the right tree before
running a model arm — an oracle F1 below 1.000 means the references do not
resolve inside the tree you named.

## 5. Skills and profiles as arms

Arms name paths directly, so an external skill or persona needs no root flag.
Arm names are derived from the file or directory name, not the full path:

```console
$ python -c "from dokimi.treatments import create_treatments; [print(a.name) for a in create_treatments(['baseline','skill:C:/Users/Public/content-repo/skills/demo-skill','profile:C:/Users/Public/content-repo/agent_profiles/demo-expert.md'])]"
baseline
skill:demo-skill
profile:demo-expert
```

A path that does not exist fails immediately, before any model call:

```text
FileNotFoundError: Skill path does not exist: C:\Users\Public\no-such-checkout\skills\demo-skill
```

A subject descriptor bundles the artifact with the suite it should be measured
on, and may point at the external checkout throughout. `catalog show --kind subject`
resolves it — including the arm specs it will run — without executing
anything:

```console
$ python cli.py catalog show --kind subject C:/Users/Public/content-repo/demo-subject.toml
{
  "id": "demo-skill",
  "subject": {
    "kind": "skill",
    "ref": "C:/Users/Public/content-repo/skills/demo-skill",
    "members": []
  },
  "suite": {
    "products": ["demo"],
    "questions": ["C:/Users/Public/content-repo/questions/demo_golden.json"],
    "tasks": ["demo-widget"]
  },
  "baseline": "baseline",
  "awareness_arm_specs": [
    "baseline",
    "skill:C:/Users/Public/content-repo/skills/demo-skill"
  ],
  "work_arm_specs": [
    "baseline",
    "skill-agent:C:/Users/Public/content-repo/skills/demo-skill"
  ],
  ...
}
```

(Reformatted here for width; the command prints one JSON key per line.)

`suite.tasks` entries are resolved the way `tasks run --tasks` resolves them: a
bare name such as `demo-widget` needs a root, so `subjects run` takes the same
`--tasks-root <checkout>/evaluation/harbor/tasks`. A full path to a task
directory works without it.

## 6. Products this repository has never heard of

The committed registry is the bundled `dokimi/products.yaml`. A product outside it
is unknown, and `benchmark` says so rather than inventing one:

```console
$ python cli.py benchmark preflight --library demo
Error: "Product 'demo' not found in registry. Available: advisor, dpctl, dpnp, ..."
```

Supply the product with `--registry <checkout>/products.yaml` (as in section 2).
An entry there can declare `context7_id:`, which is what documentation and MCP
arms resolve against.

There is no name-to-library-id guessing. If a documentation arm cannot find an
id, it stops and tells you both ways to give it one:

```console
$ python cli.py answers generate --product widgetlib \
    --questions C:/Users/Public/content-repo/questions/demo_golden.json
Loaded 2 questions from C:/Users/Public/content-repo/questions/demo_golden.json
Documentation source: context7
Error: 'widgetlib' is not a Context7 library ID. Context7 IDs look like 'uxlfoundation/oneTBB' (GitHub repo) or 'intelpython_github_io_dpnp' (docs website), and cannot be derived from a product name. Either declare 'context7_id:' for the product in products.yaml, or pass --context7-id on the command line.
```

Resolution order for documentation and MCP arms is: explicit
`--context7-id`, then the `;id=` of an `mcp:` source, then the product's
`context7_id:` in the registry, then the error above.

A *registered* product that declares no `context7_id:` is the same error, named
per product. That case needs its own check because a Context7 website id looks
like `host_path` — a registry key with an underscore in it (`mkl_extensions`)
would satisfy that shape and be sent as an id, retrieving nothing while the run
still reports a documentation arm.

## 7. What happens when a root is missing

Worth knowing before you read a suspiciously clean result:

| Command | Root missing | Exit |
|---|---|---|
| `catalog check --root <missing>` | `Error: --root is not a directory: <path>` | 2 |
| `catalog check --root <no SKILL.md> --kind skills` | `no SKILL.md found under <path>` | 1 |
| `tasks run --tasks-root <missing>` | `Error: --tasks-root is not a directory: <path>` | 2 |
| `eval tasks list --tasks-root <missing>` | `Error: --tasks-root is not a directory: <path>` | 2 |
| `eval tasks list --format localization --tasks-root <missing>` | `Error: --tasks-root is not a directory: <path>` | 2 |
| `coverage` with no roots | every product reads `no`/`0`, plus an explicit note | 0 |
| `evaluate`/`benchmark run` with a root lacking the golden set | `WARNING ... has no golden set for <product>` and continues | — |

`eval tasks list` is the trap for an existing directory: an empty listing and a correct listing
both exit 0, so an empty checkout looks like a correct one. A missing root is refused with exit 2,
not a silent success. `eval tasks list --format localization` refuses an empty root with exit 2. The `coverage` matrix is explicit about it instead:

```console
$ python -m dokimi.coverage
...
Note: no --tasks-root given; the work half is unresolved.
Note: no --questions-root given; the awareness half is unresolved.
```

Use `config_check` as the CI-friendly form of the same question; it validates
the registries and, when given roots, the coverage contract:

```console
$ python -m dokimi.config_check \
    --tasks-root C:/Users/Public/content-repo/evaluation/harbor/tasks \
    --questions-root C:/Users/Public/content-repo/questions
✓ product and intent registries are consistent
```
