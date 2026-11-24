### Fixed

- **`data export` writes the dataset card for an input file whose name has a byte that is not UTF-8** — the card names the input (`Exported from \`q\xff.json\``), and the strict `write_text` raised `UnicodeEncodeError` for the lone surrogate such a name decodes to, after `questions.jsonl` was written and before `README.md` was. The card is now written like the report Markdown: the lone surrogate becomes `?`, and the bytes of a card whose text encodes are unchanged.
