<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`repo run`: a `probe` event for a command that asks the host for its facts.** A command
  such as `nproc`, `lscpu`, `nvidia-smi`, `pip list`, `printenv` or a `python -c` one-liner that
  calls `os.cpu_count()` gets a `probe` event after its `command` event, with its `target`
  (`cpu`, `gpu`, `os`, `memory`, `packages`, `env`, `network`) and the `pattern` it matched. The
  patterns match the program the shell runs, not argument text (`grep nproc README` is no probe).
  The defaults ship in `dokimi/repos/probes.py`; a manifest's `probes` key or `repo run --probes
  FILE` replaces a target's list, and `[]` turns it off. Existing event kinds are unchanged.
