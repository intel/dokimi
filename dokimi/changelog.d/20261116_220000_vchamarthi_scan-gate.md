<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`repo scan` has a gate any repository can adopt** (#851). `--baseline FILE` takes an earlier report, and `--fail-on fail` then fails only on a claim that report does not also fail (matched by file, kind and text, not line). The report's `baseline` block gives `known`, `new` and `fixed`. `.dokimi/scan.toml` is the repository's own setting: `[[ignore]]` entries (`pattern`, optional `kind`, required `reason`) mark matching claims `unresolved_ignored`, and `identifiers = "fail"` opts back in to failing on a missing bare name. `scan_config` in the report lists each entry with its hit count.
- **`report summary --format sarif`** writes SARIF 2.1.0, one result per finding at its file and line, or at its input file, with no line, when it has no file (a failed or stopped check is a result too), for GitHub code scanning, Azure DevOps, and the GitLab and Jenkins SARIF importers.

### Changed

- **A bare identifier the tree lacks is `unresolved_identifier`, not `fail`.** It may be an external API, a sibling package or a builtin, so it no longer fails `--fail-on fail` unless `.dokimi/scan.toml` sets `identifiers = "fail"`. A name that is the stem of a tracked file (`bench_fftnd` for `bench_fftnd.py`) is `ok_stem`.

### Fixed

- `repo scan` no longer reads Doxygen `///` as a Bazel label.
- `pipe-to-shell` no longer fires on `curl … | python -c …`, `| python -m json.tool`, or `| node -e …` (not `| sh -e`, `| ruby -E`, or an inline program that execs or evals, on any of its lines, which still runs what it reads; a double-quoted program is read through its `\"`, and a quoted segment glued to it is part of it; a marker word that ends a string literal or sits in a later command is not read): the program is on the command line and the download is only its input.
- `description-no-when` accepts "Use to …" and "Use this skill to …".
- `catalog formats` counts the skills of a Claude plugin in its `skills/` directory, in the directories its `skills` key names, or, with neither, in those a `marketplace.json` next to its `plugin.json` declares by `source`; a skill reached by two paths counts once, and an unreadable `marketplace.json` or a `plugins` that is not a list is a problem, not a crash.
- `memory lint` refuses a directory with no `MEMORY.md` that looks like a repository root (`.git`, `pyproject.toml`, `CMakeLists.txt`, ...), instead of linting its README and AGENTS.md as memory.
