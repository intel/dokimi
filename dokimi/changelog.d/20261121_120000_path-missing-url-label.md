### Fixed

- **`catalog check` no longer reports `path-missing` for the label of a link to a URL.** A code span used as the text of `[`examples/a.py`](https://...)` is the link's text, not a bundled file, so it is not read. A code span in the label of a local link, or outside any link, is checked as before.
