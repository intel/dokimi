### Fixed

- **An unavailable completion cost is visible in default logs.** Dokimi logs one warning per model when LiteLLM cannot compute the cost, and includes the cause. This covers missing prices and other calculation failures. The recorded `cost_usd` stays null.
