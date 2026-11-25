### Fixed

- **A negation written with a typographic apostrophe is counted by the novelty probes' paraphrase and reversal checks** — `doesn’t`, `don’t` and the like (`’`, `‘`, `ʼ`) are read as the ASCII `doesn't` before negations, a leading negative auxiliary or a trailing tag (`..., doesn’t it?`) are counted, so `Why doesn’t the skill ship measured numbers?` against `Why does the skill ship measured numbers?` is rejected as `polarity_flipped`, as the ASCII spelling already was. The anchor, length and `identical` checks still see the text as written.
