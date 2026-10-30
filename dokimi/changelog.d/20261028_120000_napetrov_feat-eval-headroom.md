### Added

- **`eval headroom` flags arms items with no headroom** — reads one or more `arms.v1` files of the same question set and flags an item as a `ceiling` when the no-skill arm's mean is at least `--ceiling` (default 0.9, on 0-1), or as a `floor` when the no-skill arm was scored, every arm that a run scored has a mean at most `--floor` (default 0), and at least one non-baseline arm scored. An item with fewer than `--min-reps` runs (default 3) behind it is `provisional`. The human table goes to stdout; `-o` writes a `headroom.v1` artifact with every item. The flags change no score.
