### Fixed

- **A manifest link that leaves the repository and comes back in is refused alike from a directory and from `<dir>@HEAD`.** A committed link such as `.agent-eval/rt.yaml -> ../../repo/real.yaml` was read from the directory (it resolves inside it) and refused from `<dir>@HEAD` with `path escapes the repository`. The directory now refuses it too: a link whose `..` goes above the root escapes, whatever it resolves to. An absolute link into the directory is still followed from the directory.
