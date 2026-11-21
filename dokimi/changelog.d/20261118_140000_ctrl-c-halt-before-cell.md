### Fixed

- **`dokimi repo run` no longer prepares or starts a cell after Ctrl-C has set the halt** — the interrupt now sets the halt before it cancels the queued cells, and a cell a worker takes after that is dropped before it reserves budget or prepares a workspace, so a resume runs it again.
