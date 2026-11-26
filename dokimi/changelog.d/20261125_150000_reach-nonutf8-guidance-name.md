### Fixed

- **`repo reach` builds its probe workspace for a guidance file whose name has a byte that is not UTF-8** — the seeded file carries the tree path in its `Verification token` lines, and the strict `write_text` raised `UnicodeEncodeError` for the lone surrogate such a name decodes to, for a tree file and for a shadow file alike. Both are now written like the report Markdown: the lone surrogate becomes `?` in the label line (scoring matches the tokens, not the path), and the bytes of a file whose text encodes are unchanged.
