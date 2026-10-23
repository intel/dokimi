### Fixed

- **`repo inspect` records a nested `.cursor/rules/*.mdc`** — a rule file
  under a subdirectory, such as `web/.cursor/rules/style.mdc`, was not
  listed in `instructions`, while Cursor applies it to the files under that
  directory. It is now a `path-specific` record whose `directory` is `web`;
  its activation stays `unknown`, since the rule's frontmatter sets it.
