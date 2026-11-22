### Fixed

- **judge sampling refusals come before any answer is paid for** — `skill benchmark`, `skill evaluate`, the judge panel and `Judge` now refuse a judge `--judge-temperature` or seed that the judge's own route refuses (for example `temperature 0` on Haiku 5.5 through Bedrock), at start instead of failing every judgement. Route checks use the litellm value rules, so a value the route refuses is reported with the model's name.
