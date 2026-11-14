### Fixed

- **An atoms file with `status: "partial"` and no `partial` block is refused** — `load_atoms_record` (and so `probe`, `shorten`, `view` and the other atoms readers) raised `InputError` only when the `partial` key was present, so `{"schema_version": "novelty-atoms.v1", "status": "partial"}` was read as a finished, empty atom set. It now refuses either marker. A file marked partial with no `partial` block (which no run writes: it is truncated or hand-edited) is refused with a message about a truncated or hand-edited file, without cap wording.
