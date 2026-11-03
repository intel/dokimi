<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`benchmark preflight` and the RAGAS judge read a `file:<path>` key as the run does** — the
  answerer and the judge read the key from the file; these two kept their own table of key
  variables and took the variable's value as it was:
  - `benchmark preflight` printed "Preflight OK" when the key variable named a file that is
    missing, unreadable or empty, and every call of the run then failed on it. It now refuses
    with exit 2, as it refuses a missing key: "Cannot read API key from file '…'
    (ANTHROPIC_API_KEY, provider anthropic)" or "ANTHROPIC_API_KEY for provider anthropic names
    an empty key file".
  - The RAGAS judge (`answers generate --ragas`, `eval ragas`) sent the `file:…` string itself
    as the key, from the environment or from an explicit `api_key`, so every RAGAS call failed
    authentication; it now sends the file's contents. An unreadable key file now ends the RAGAS
    step before any RAGAS call, with "RAGAS evaluation failed: Cannot read API key from file …"
    like any other RAGAS failure. In the Python API, the fallback judge that a provider other
    than openai and anthropic gets reads its key file too.
