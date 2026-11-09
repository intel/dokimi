<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo validate`'s note on tasks whose base holds no file the guidance globs match names the arms it covers** — it said every `guidance:*` arm is the same workspace there and to leave those tasks out of arm comparisons, which is wrong for `guidance:rev:<rev>` and `patch:<file>` arms and `+skill:` parts: they bring their own files, so following the note on replay tasks, whose bases are usually older than the guidance they test, left every task out. The note now says that there `guidance:none`, `raw` and `bridge` give the same workspace and `as-skill`, `overlay` and `format=` fail the cell's preparation, so the tasks it lists stay out of comparisons among those arms only. It also no longer lists a task whose base holds an `AGENTS.<agent>.md` overlay the globs miss: `guidance:none` removes that overlay and `guidance:overlay` keeps it whatever the globs match, so there `none` differs from `raw` and the overlay arm prepares. Nor does it list a task whose `setup_patches` name such a file, as `git apply` reads them, or are no patch: they apply before the arm, so a patch that adds an overlay does the same. A task whose setup patches name only other files, code for example, stays listed.
