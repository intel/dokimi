### Fixed

- **`repo reach` no longer plans a FIFO as a guidance file or calls it a link.** In a plain directory, a FIFO, socket or device named like guidance (for example `sub/AGENTS.md`) was planned, and a lone FIFO `AGENTS.md` was refused as a link to nothing. They are now left out of the listing, as in a git working tree; when nothing else matches, the refusal says `each is not a regular file`, and a path that resolves to one is refused as `is not a regular file`. `repo inspect` lists a file it could not look at in `not_observed`. (#1273)
