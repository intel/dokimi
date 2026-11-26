<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A directory that cannot be listed is an error where seven more places listed a smaller tree** — `Path.rglob` and an `os.walk` with no `onerror` skip a subdirectory that cannot be listed (mode `0o111`) without an error, so the task cache key of a Harbor export, the files `eval leak` indexes, the `resources` of a loaded skill, the packages `catalog formats` lists, the scenarios of a reach fixture, the link check of a repo-run skill arm and `eval leak`'s output-overwrite guard (which lists the skill only when `--out` or a lock is written) described a smaller tree. Each now raises the `OSError` of the directory (`catalog formats` and `eval leak` exit 2 and name it). An arm spec `skill:`, `skill-agent:` or `skill-catalog:` that names such a skill is refused as a `ValueError` that names the arm. A catalog scan row for a skill with such a folder is an error row; a hidden or vendored folder (`.git`, `venv`, `node_modules`) that cannot be listed is still left out of a skill's resources. The result for a tree that can be listed is unchanged.
