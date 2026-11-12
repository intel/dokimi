### Fixed

- **A `guidance:rev:` link to a path that is not UTF-8 installs its target** — on POSIX an `AGENTS.md` symlink whose target holds a byte such as 0xff (`r\xff/GUIDE.md`) read as `r�/GUIDE.md`, and the arm failed with `PrepError: ... links to r�/GUIDE.md, which is not a file there`. The target is read as git lists the entries now (`os.fsdecode`), so the arm installs the target's content at `AGENTS.md`. A UTF-8 target is unchanged.
