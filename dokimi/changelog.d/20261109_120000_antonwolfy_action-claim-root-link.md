### Fixed

- **`repo scan` judges an Action-shaped name below a root link to a directory as a path** — with
  a root link `owner -> src/pkg` and a workflow step `uses: owner/name@v1`, a guidance claim
  `owner/name` that the tree does not hold read `ok` (`used by .github/workflows/ci.yml`), though
  a checkout makes `owner/` a directory and a real `owner/` keeps such a name a path. A root link
  that resolves to a directory now counts as that root directory, so the claim keeps its path
  verdict (`unresolved`, `tried owner/name`) and its reason names no workflow the Action reading
  skipped. A link to a file is still no directory.
