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
  left by a killed run is removed by a later clone of that URL once it is
  older than the clone timeout (30 minutes), also when the clone is already
  cached; one that cannot be removed is logged, and so is a partial the
  losing run cannot remove, which then uses the other run's clone.
