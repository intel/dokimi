### Fixed

- **`eval check --schema-only` refuses a patch link that a run would refuse.** A `setup_patch` or `review_patch` link that leaves the repository and comes back in ended in a traceback (exit 5), and one that points to a file outside the task directory passed. Both are now a validation error naming the patch (exit 2, not 1), the same for a directory and for `<repo>@HEAD`.
