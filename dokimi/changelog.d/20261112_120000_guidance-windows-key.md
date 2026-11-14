### Fixed

- **A guidance snapshot on Windows keeps the real file when another git name decodes alike** — when a commit held `l\xffint/AGENTS.md` and `l�int/AGENTS.md`, both took one key and the later blob replaced the earlier, so the snapshot could read the wrong blob. The listing is now split on raw bytes and keyed by the exact file name, a name Windows cannot decode is skipped, and a symbolic link whose target is such a name is refused as a link to no file. On Linux and macOS nothing changes.
