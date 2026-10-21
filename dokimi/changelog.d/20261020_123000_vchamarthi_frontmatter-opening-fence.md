### Fixed

- **A `---` look-alike on the first line no longer opens frontmatter** —
  `parse_frontmatter` now requires the opening fence to be exactly `---`, the
  same rule it applies to the closing fence. A document that starts with a
  thematic break such as `-----` and has a `---` line later keeps its leading
  text in the body. An unterminated block with a leading BOM is returned
  without the BOM, like every other branch.
