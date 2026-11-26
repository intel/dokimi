### Fixed

- **Unknown skill frontmatter keys** such as `tags` and `author` now get a `frontmatter-unknown-key` note because another harness may read them. A skill with only notes reads as `ok`. The `allowed_tools` spelling gets a warning under the same code and points to `allowed-tools`, the Claude Code grant key. The default output shows both levels, and `--fail-on-code frontmatter-unknown-key` still exits 1 on either.
