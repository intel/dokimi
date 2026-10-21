### Added

- **Task `host` requirements** — a `task.json` can declare
  `"host": {"min_cpus": 4, "cpu_vendors": ["GenuineIntel"], "cpu_flags": ["avx512f"]}`
  (every key optional) for a hidden test that can only show its bug on some hardware, such
  as a thread race. `repo tasks check` and `repo run` probe once, in a container of the
  evaluation's image, what such a container sees: CPUs are its affinity mask capped by its
  cgroup CPU quota (`docker --cpus`), vendor and flags come from `/proc/cpuinfo`. A task the
  host cannot show is `unsupported` with the reason in `tasks check` (which exits 1, as for
  an egress task, unless `--allow-unsupported` is given, for CI on hosts that cannot show
  some bugs; broken tasks, egress tasks and tasks whose host probe failed still exit 1), and in `repo run` its cells are not run: each holds an `unsupported.json`,
  and `repo report` lists them apart, neither failed nor missing; if the probe itself
  failed, `repo run` exits 1. A later run on a host that
  meets the requirements runs them. The probed facts are recorded in `check.json` `host`, in
  the batch's `_eval/host.json` and in every cell's `meta.json` `host` (or its `unsupported.json` `host` when the cell is not run). A `host` value of the
  wrong shape is refused before any container starts.
  `repo validate` reports it too, and refuses a `cpu_vendors` name `/proc/cpuinfo` does not
  use (`GenuineIntel`, `AuthenticAMD`, `HygonGenuine`, `CentaurHauls`, `Shanghai`); vendors
  and flags match in any case. A `/proc/cpuinfo` the probe could not read is a probe failure,
  not a hardware miss. A failed probe writes no `unsupported.json`, so `repo report` counts the
  cells missing. On a host miss `tasks check` still prepares the workspace and probes isolation
  (and runs the oracle when only CPUs are short); it skips the null check. `--warm` does not
  judge the host.
