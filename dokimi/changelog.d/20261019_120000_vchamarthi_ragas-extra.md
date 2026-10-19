### Changed

- **RAGAS is an optional extra**: `pip install dokimi` no longer installs
  `ragas`, `langchain-community`, `langchain-anthropic`, `langchain-openai` or
  `langchain-core`. `eval ragas` and `answers generate --ragas` need
  `pip install 'dokimi[ragas]'` (the `dev` extra includes it). Without the
  extra, both exit 1 with the install command before any model call.
