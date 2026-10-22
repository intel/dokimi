### Fixed

- **The isolation probe of `repo tasks check` writes its task list to a `mktemp` file** and removes
  it on exit, instead of a fixed file in `/tmp` that it left behind. Two probes on one machine
  no longer share the file, and a file another user owns no longer breaks the probe.
