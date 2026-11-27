<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`hidden-unicode` reports a hidden character that YAML decoding produces** — a bidi control, tag character or
  zero-width character written as an escape (`"\u202e"`, `"\U000E0041"`) in a double-quoted value of the `SKILL.md`
  front matter, under any key, is a `warn` that names the key (#1334, #PR_NUMBER). Single-quoted and plain values,
  which YAML does not decode, and a literal character, which was already reported once, are unchanged.
