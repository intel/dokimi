---
depends_on:
  - dokimi/dokimi/cli_flags.py
  - dokimi/tests/test_cli_flags.py
  - dokimi/tests/data/cli_flags_allowlist.txt
  - dokimi/scripts/cli_surface.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Adding or renaming a CLI flag

Every concept a `dokimi` flag can name is one row of `CONCEPTS` in
`dokimi/cli_flags.py`: its canonical spellings, its type and the synonyms banned
for it. `tests/test_cli_flags.py` walks every parser of `dokimi.cli.build_parser()` and
fails when a flag is neither a canonical spelling nor a row of
`tests/data/cli_flags_allowlist.txt`. A banned synonym fails too, with the canonical
spelling in the message.

Until the flags move onto the registry, `cli_flags.py` is data only: no command reads it.

## Adding a flag

Use the canonical spelling of its concept. A new concept needs a row in `CONCEPTS`,
which reviewers look at.

Then regenerate the snapshot. From `dokimi/`, after `make dev`, run:

```bash
make cli-snapshot
```

and commit `docs/reference/cli-interface.json` with the change. CI's `typecheck` job
(shown as `mypy`) and `tests/test_cli_flags.py` check it against the code, so a flag
change without a regenerated snapshot fails the PR.

The ratchet compares spellings only. An option's type and action are not compared
with the `kind` in its registry row; a reviewer checks that the type matches the concept.

Only if the flag must keep a non-canonical spelling, add one row to
`tests/data/cli_flags_allowlist.txt`, with the reason after `#`, for example
`--sample-size  # the spelling the external runner already passes`.

## Removing or renaming a flag

An allowlist row that no parser uses fails the test, so delete the row when the flag
goes. A row for a spelling that is now canonical fails too. Either way the list keeps
no stale row.

## Why the snapshot test runs on one Python

The dumper hashes the source of validators such as `positive_int` with `ast.dump`, whose
output differs between Python versions. The snapshot is therefore written by the pinned
interpreter (`make dev`, Python 3.13), and its content check runs on that interpreter only:
the `typecheck` job on every PR, and the Python 3.13 test leg on `main`. The other test legs
run the ratchet and the structural checks.

An allowlist row exempts its spelling on every command, not only the one it was added for.
A new command may therefore use a spelling that is already allowlisted, even a banned one.
The check catches only spellings that are neither canonical nor allowlisted, and banned
ones that are not allowlisted.
