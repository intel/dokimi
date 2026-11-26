### Fixed

- **`eval check` refuses a patch link that `repo run` would refuse.** A `setup_patch`, `setup_patches` or `review_patch` that is a symlink to a file outside its task directory passed the check, and one that left the repository and came back ended in a `SourceArgumentError` traceback with exit 5. Both are now an error naming the patch, and `eval check --schema-only` exits 2 for them (other task errors still exit 1).
