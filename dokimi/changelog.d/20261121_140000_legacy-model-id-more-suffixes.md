### Fixed

- **`catalog check` reads 12 more bundled suffixes for retired model IDs and hidden characters** — a bundled `.bash`, `.zsh`, `.ps1`, `.mjs`, `.tsx`, `.html`, `.xml`, `.csv`, `.jsonl`, `.mdx`, `.cfg` or `.ini` file was skipped, so `legacy-model-id` and `hidden-unicode` found nothing in it; they are now read.
