### Fixed

- **A skill or task file whose name is not UTF-8 no longer stops its digest** — the digest of a skill directory ref in a subject scorecard and the content key of a task exported for Harbor raised `UnicodeEncodeError` on a file named, say, `n\xff.md`; both now hash the bytes of the name on disk, as `os.fsencode` gives them, and the digest of a tree whose names are UTF-8 is unchanged.
