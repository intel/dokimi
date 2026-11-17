### Fixed

- **`skill novelty items` names the stop of a partial probe result that a call with no price ended** — it refused every partial probe file but a provider-error one with "--max-usd stopped the measurement", advice that does not cure a `no_price` stop. It now reads the stop's own message for every reason but `spend_cap`, and a `no_price` block with no message says to price the model or drop `--max-usd`. (#789)
