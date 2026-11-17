### Fixed

- **`eval check` and `repo run` refuse a `--cache-root` or a `--cache NAME=PATH` that is not a usable directory with exit 2 naming the flag and the path** — a regular file, a path below one, or a `--cache-root` holding a regular file named after a cache used to end in `✗ [Errno 20] Not a directory` and exit 1 after the image build; the paths are now checked before the export and the build. An existing read-only directory still passes.
