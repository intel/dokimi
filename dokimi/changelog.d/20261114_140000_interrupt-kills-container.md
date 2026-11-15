### Fixed

- **Ctrl-C during `dokimi repo run` kills the cell's container by name** — before, only the docker CLI was stopped, so an agent or grader that ignores SIGTERM kept running (and spending) after dokimi exited. A timeout already did this.
