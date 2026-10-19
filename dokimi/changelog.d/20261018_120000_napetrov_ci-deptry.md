### Changed

- **Install dependencies follow the imports** — `pip install dokimi` no longer
  installs `beautifulsoup4` (and its `soupsieve`), which nothing imports, and no
  longer lists `anthropic` itself: it still comes in through
  `langchain-anthropic`. `langchain-openai` and `langchain-core`, which
  `eval ragas` imports, are now declared (`>=1.0,<2`) instead of arriving only
  through `ragas`. Code that imported `bs4` from dokimi's environment installs it
  itself.
