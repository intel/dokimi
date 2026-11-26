### Fixed

- **`catalog check` misreads three command mentions** — `install-unpinned` no longer counts the words of a sentence in a `.json` file as packages (it reads each string up to its first `, ` or `. `) and no longer counts `npx tsc` in a file that names `typescript`; `pipe-to-shell` no longer fires on `curl | sh` with no argument for `curl`, such as a code span that forbids it.
