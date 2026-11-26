### Fixed

- **A `Don't` question is no longer accepted as the reversal of a `Do not` statement** — `check_wording("reversal", ...)` kept a question's leading negative auxiliary only when the source opened with a contracted one, so `Don't run the installer as root?` against `Do not run the installer as root.` counted no negation and read as a flipped polarity. A source that opens with `Do not`, `Does not`, `Never`, `Cannot` and the like now keeps the candidate's leading negative auxiliary too, and the candidate is rejected as `restates_source`, as it was for a source opening with `Don't`.
