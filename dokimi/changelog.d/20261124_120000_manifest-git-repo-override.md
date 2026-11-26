### Fixed

- **The run manifest's `git` block no longer reads another repository when `GIT_DIR` (or `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_INDEX_FILE`) is set** — the manifest's `sha`, `branch` and `dirty` ran git with the caller's environment, so a hook or pre-commit that exported one of those variables recorded another repository's commit, branch or dirty flag in the provenance of a benchmark run. Those four variables are now left out of the git calls, as the other git callers in dokimi already do. Without them the output is unchanged.
