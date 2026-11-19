### Fixed

- **`trace prefilter` no longer lists Markdown scaffolding as rules** — an underlined (Setext) heading, a four-space indented code block and an HTML comment such as a licence header are now skipped, as `#` headings and fenced code already were. A licence comment no longer yields a `kept_protected` row for `Apache-2.0`. `prior to` now counts as an ordering constraint, like `before`, so such a rule is tagged `ordering`. The `novelty` commands share the segmentation: span ids shift on files with these constructs, and a new span kind `comment` appears in their span lists.
