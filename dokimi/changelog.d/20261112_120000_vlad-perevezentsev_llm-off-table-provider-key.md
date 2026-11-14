<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A provider outside dokimi's key table is no longer sent `OPENAI_API_KEY`** — a litellm route
  such as `azure` or `mistral` was sent the OpenAI key from `eval score panel --judge-configs`,
  `eval triggers generate --rewrite --provider`, an `LLM` built directly and a `QuestionValidator`
  from Python. Without an explicit `api_key` it now sends none, and litellm reads the vendor's own
  variable (`AZURE_API_KEY`, `MISTRAL_API_KEY`). `llm.credential_env_var` returns `None` for such a
  provider. `openai` and the other providers in `llm.PROVIDERS` are unchanged.
