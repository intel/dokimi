### Fixed

- **`repo run` keeps the agent's git config across a crash, and two runs can
  clone the same source at once** — a run killed after it created
  `{run_dir}/agent-git/` and before it moved the workspace's `.git/config`
  and hooks there made the next grade of that cell delete them unarchived,
  as a regrade does. The moves now go to a temporary directory renamed to
  `agent-git/` after the last one, and the next grade finishes them. A
  regrade of a workspace whose `.git` is a file no longer overwrites the
  archived `agent-git/dot-git` with whatever a grader left there. Two
  processes cloning one remote source into the repo cache
  (`$DOKIMI_REPO_CACHE`) shared one `.partial` directory, so the second
  deleted the first's clone or failed on the rename; each now clones into
  its own directory, and the one that loses the rename uses the other's
  clone. The same holds for the full clone that task bases are checked out
  from.
