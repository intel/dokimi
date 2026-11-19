### Fixed

- **`eval check` refuses a deeply nested `task.json` or suite file, and a `--manifest-file` that is not a regular file.** Before, a `task.json` or suite file of 100000 nested arrays ended in a `RecursionError` traceback (exit 5), and a named pipe as `--manifest-file` blocked until killed. Now each is refused with a message naming the file, without a traceback: a task gets one line (exit 1), a suite the usual `invalid suite_manifest.v1` refusal (exit 1), a manifest file one line (exit 2).
