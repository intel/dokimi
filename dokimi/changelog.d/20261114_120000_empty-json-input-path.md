### Fixed

- **An empty path given to a JSON input flag is refused by name** — `eval score reference --answers ""` exits 2 with `Error: --answers "": no file given`, where it read `.` and said `--answers : Is a directory`. Every other flag and argument that reads a JSON file through the shared reader gets the same refusal, and a read without a flag says `cannot read "": no file given`.
