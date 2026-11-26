### Changed

- **Internal refactor of `eval judge validate` and the task-run summary, no behaviour change.** The command's argument checks, scorer construction and inconclusive warnings, and the task-run summarizer's per-row and per-harness steps, now live in separate helpers. Flags, output, exit codes and the summary's fields and key order are unchanged.
