### Fixed

- **A panel judge score too large for a float is a failed reply, not a stray conversion error** — a score such as `10**400` in a judge's JSON reply raised `OverflowError` from `float()` inside the panel's reply check. It now reads as the same formatting failure NaN and Infinity already are: the reply is retried, and a role with no usable reply records `judge reply has a non-numeric or non-finite score` as its error. Votes, retries and the scores of every other reply are unchanged.
