### Fixed

- **A faithful paraphrase that adds or drops a question tag or a leading "Doesn't" is no longer dropped as `polarity_flipped`** — the novelty probes' paraphrase check strips a leading negative auxiliary and a trailing tag (`..., doesn't it?`) from both wordings before it compares negation presence, as the reversal check already did. Such wordings no longer count in `malformed_rate`. A spelled-out `not` or a mid-clause `doesn't` still counts.
