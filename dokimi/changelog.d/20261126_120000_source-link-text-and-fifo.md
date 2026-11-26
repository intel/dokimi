### Fixed

- **`repo scan`, `repo budget` and `repo inspect` handle unreadable guidance links and FIFOs** — a committed link whose text is not UTF-8 is now reported as `guidance file not scanned` instead of ending in a `UnicodeDecodeError` traceback; a link whose target is a file followed by `/` (`nul.md -> real.md/`) is no longer counted as that file, as in a checkout; and a FIFO guidance file in a plain directory no longer blocks the command: it is reported as `not a regular file` rather than `links to nothing` (#1272, #PR_NUMBER).
