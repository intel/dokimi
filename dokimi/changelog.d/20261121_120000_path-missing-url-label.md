### Fixed

- **`catalog check` no longer reports `path-missing` for the label of a link to a URL.** A code span used as the text of an inline link to a URL (`[`examples/a.py`](https://...)`) or of a full reference to one (`[`examples/a.py`][ref]` with `[ref]: https://...`) is the link's text, not a bundled file, so it is not read. A code span in a shortcut or collapsed reference (`[`examples/a.py`]`, `[`examples/a.py`][]`), in the label of a local link, or outside any link, is checked as before.
