<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Fidelity golden: what a docker-claude arm sends

`claude-code-2.1.318/` holds the request the pinned Claude Code sends for the two
golden cases of a docker-claude arm, for one fixed task (`Fix the off-by-one in sum().`):

- `bare.json`: the arm with no skill. System prompt, the seven built-in tool schemas, the
  user message (the instruction as written, with the environment and model reminders).
- `skill.json`: the arm with `skill/sum-fast/` loaded through `--plugin-dir`. The same, plus
  the skill listing with that skill's own entry.

Each file is the normalized request (`dokimi.harnesses.fidelity.normalize_request`):
dates, versions, ids, the request's own model, the work dir and host facts are blanked to tokens
such as `<DATE>`, `<VERSION>`, `<UUID>`, `<MODEL>`, `<WORKDIR>` and `<HOST>`. The other model ids
the system prompt names, and the raw `anthropic-beta` and `anthropic-version` headers, are kept as sent.
`_meta` records the version and the case. `skill/sum-fast/SKILL.md` is a fixture, not a
skill dokimi ships.

## What the tests check

`tests/test_fidelity_golden.py` builds each arm's `claude -p` argv with the arm's own
builder (`DockerSolverHarness._claude_args`), runs the pinned `claude` against a recording
server (no model is called), normalizes the request and:

1. `payload_audit.new_leaks` against the golden finds no dokimi marker (arm name, question
   id, judge or rubric text, and so on) that the golden does not already have;
2. the normalized request equals the golden, or the test prints the diff.

The golden tests skip when `claude 2.1.318` is not on PATH. CI runs them in the
`fidelity` job (`DOKIMI_FIDELITY_REQUIRED=1` makes a missing binary a failure), which
installs `@anthropic-ai/claude-code@2.1.318` from npm.

## Refreshing the golden

Take it with the native build in a clean container, not on a host:

```
python3 scripts/refresh_fidelity_golden.py            # debian:trixie-slim, pinned version
python3 scripts/refresh_fidelity_golden.py --binary /path/to/claude-2.1.318
```

The script refuses a binary whose version is not the pinned one. Then:

1. `git diff tests/fixtures/fidelity` and read it: a change to the system prompt, a tool
   schema or the skill listing is a change of what the arm sends; say so in the PR.
2. Run `pytest tests/test_fidelity_golden.py` (with `DOKIMI_FIDELITY_REQUIRED=1` and
   `DOKIMI_FIDELITY_IMAGE` set, to run the client in the same container image).
3. To move to a new Claude Code release, change `PINNED_CLAUDE_VERSION` in
   `dokimi/harnesses/fidelity.py`, the CI job's npm pin and the golden directory name
   together, and refresh.

## Known differences

- The client sends a token-budget reminder and a commit-attribution reminder; both are
  part of the golden, and the skill listing is compared as sent, so a skill a clean capture
  gains (a broken seal) is a diff. On a host whose client adds reminders or skills of its
  own, set `DOKIMI_FIDELITY_OPERATOR_HOST=1`: the normalizer then removes the reminders by
  name (`OPERATOR_REMINDERS`) and the skill entries other than the fixture's. Anything else
  is pinned.
- The arm runs in a container with its own paths and user. The host run uses the same
  argv and the same flags; the paths are blanked, so the comparison holds.
