### Added

- **The host probe fingerprints the machine with a registry of collectors** — `os`, `cpu`, `memory`, `gpu`, `python`, `tools`, `network` and `virt`, each a flat dict under `collectors` (`collectors_version` 1), beside the container's unchanged keys. A failing collector reports `{"error": ...}` and the others still run; no host or user name, IP address, environment value or absolute path is recorded.
- **`repo run --label-host` runs a task the host misses instead of blocking it** — each cell records `host_match` (per requirement met, unmet or unknown) in `result.json` and `meta.json`, and `repo report` groups results by host fingerprint. Without the flag, `repo report` still splits rows by host fingerprint when a batch's cells ran on different hosts.
