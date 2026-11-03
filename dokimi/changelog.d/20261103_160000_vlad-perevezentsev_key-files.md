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
  - The RAGAS step (`answers generate --ragas`, `eval ragas`) sent the `file:…` string itself
    as the key, from the environment or from an explicit `api_key`, so every RAGAS call failed
    authentication. That was the judge's key, and also `OPENAI_API_KEY`, which ragas uses to embed
    for `answer_relevancy` whatever the judge. Both now send the file's contents; the embeddings
    keep ragas's default model, `text-embedding-ada-002`. A key file that is unreadable or empty
    now ends the RAGAS step before any RAGAS call, with "RAGAS evaluation failed: Cannot read API
    key from file …" or "… names an empty key file", like any other RAGAS failure. In the Python
    API, the fallback judge that a provider other than openai and anthropic gets reads its key
    file too.
  - A `file:` key that is not UTF-8 is reported as "Cannot read API key from file '…'" too, by
    every command that reads a key; it raised a bare decode error that did not name the file.
