### Fixed

- **Two `repo run`s can clone and fetch the same remote source at once** —
  two processes cloning one remote source into the repo cache
  (`$DOKIMI_REPO_CACHE`) shared one `.partial` directory, so the second
  deleted the first's clone or failed on the rename; each now clones into
  its own directory, and the one that loses the rename uses the other's
  clone. The same holds for the full clone that task bases are checked out
  from. A branch or tag source fetched by two runs at once failed one of
  them with `cannot lock ref` (or `incorrect old value provided`, `reference
  already exists`, as newer git words it) whenever the origin had moved;
  that fetch is now run again, and finds the refs current. A partial clone
  left by a killed run is removed by the next clone of that URL, also when
  the clone is already cached; one that cannot be removed is logged.

- **`repo tasks check` keeps the agent's `.git` file aside for the grade** —
  when a task's workspace has a `.git` file instead of a directory, the
  grade after the oracle moved whatever `.git` file the oracle left over the
  archived `{run_dir}/agent-git/dot-git`; the first one is kept now. The
  set-aside of the workspace's git config also goes through a temporary
  directory renamed to `agent-git/` after the last move, so a later call on
  a run dir where it was cut short finishes it instead of deleting what was
  not archived yet. No command grades such a run dir today: `repo run`
  reruns a cell that has no `result.json` from a fresh run dir, and `repo
  tasks check` always starts from one, which also covers a run dir an
  earlier version left with an empty `agent-git/`.
