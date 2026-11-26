### Fixed

- **`catalog check` no longer misreads a JSON sentence, a quoted `curl | sh` or `npx tsc`** — `install-unpinned` read the words after `pip install pkg,` in an `evals.json` string as packages; it now reads each JSON string up to the end of its sentence. `pipe-to-shell` no longer fires on `curl | sh` with no argument, such as a sentence that forbids it in a code span. `npx tsc` is not counted in a file that installs `typescript`. The `bundle-file-unreferenced` message no longer says the agent finds the files only by browsing; it says no document names them (#969, #PR_NUMBER).
