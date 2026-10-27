### Fixed

- **`--provider openai` reaches an OpenAI-compatible endpoint with any model name** — a
  model that litellm does not list as OpenAI's (`glm-5.3` behind `OPENAI_API_BASE`, an
  `org/name` id such as `meta-llama/Llama-3.1-8B`, a model newer than the installed
  litellm) was sent bare, and litellm refused it ("LLM Provider NOT provided") before any
  request; a bare `claude-*` name went to Anthropic instead. Such names now go out as
  `openai/<model>`, on every path that takes the openai provider: answers, the judge and
  panel, `eval cross-judge --judges openai/<model>` (#136), whose arms read NOT MEASURED
  before, and question and persona generation. A name litellm already sends to OpenAI
  (`gpt-*`, `o*`), or one with a litellm provider prefix (`azure/…`), keeps its route and
  its response-cache entries. The double-prefix workaround (`openai/openai/<model>`) still
  works.

### Documentation

- **README: running against an OpenAI-compatible server** — provider `openai` with
  `OPENAI_API_BASE` / `OPENAI_BASE_URL`, a placeholder `OPENAI_API_KEY` for a server that
  ignores it, and why cost is unknown and `--max-usd` stops after one call.
