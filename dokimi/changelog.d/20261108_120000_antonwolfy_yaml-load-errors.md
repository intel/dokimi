<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A YAML file with a date that does not exist (`2026-99-99`), an integer of more than 4300 digits or nesting too deep for the parser is refused like any other invalid YAML, not with a traceback** — PyYAML raises `ValueError` or `RecursionError` for these, not `yaml.YAMLError`. The `--harness` profile (`repo budget`, `repo reach`, `repo skill-reach`, ...), the `repo skill-reach --catalogue`, the `trace consistency --contract`, a benchmark spec read by `python -m dokimi.schema_check`, `library --registry` and `catalog intents --intents`, and a local `config/products.yaml` read by `answers generate` now exit with the message they give for a YAML syntax error. `catalog check` reports such a `SKILL.md` as a `frontmatter-parse` error; `catalog formats` and the other lenient frontmatter readers read it as having no frontmatter, so `repo budget` now counts its body instead of calling it not readable; `repo scan` reads such a `.pre-commit-config.yaml` as declaring no hook, and an OKF doc source keeps such a card with its body only.
