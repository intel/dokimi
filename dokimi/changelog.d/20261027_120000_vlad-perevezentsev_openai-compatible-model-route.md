### Fixed

- **`--provider openai` reaches an OpenAI-compatible endpoint with a model litellm does not
  know as OpenAI's** — such a model (`glm-5.3` behind `OPENAI_API_BASE`, an `org/name` id
  such as `meta-llama/Llama-3.1-8B`, a model newer than the installed litellm) was sent bare,
  and litellm refused it ("LLM Provider NOT provided") before any request. Such names now go
  out as `openai/<model>`, on every path that takes the openai provider: answers, the judge
  and panel, `eval cross-judge --judges openai/<model>` (#136), whose arms read NOT MEASURED
  before, and question and persona generation. A name litellm takes for OpenAI's
  (`gpt-6.1-sol`, `o3`, a `ft:gpt-4o-mini:…` fine-tune), or one with a litellm provider prefix
  (`azure/…`, `ollama/…`), keeps its route and its response-cache entries. The double-prefix
  workaround (`openai/openai/<model>`) still works.
- **`--provider openai` no longer hands a bare name to another cloud with the OpenAI key** —
  a bare name that litellm assigns to another provider (about 450 in litellm 1.106: `claude-*`
  to Anthropic, `amazon.nova-pro-v1:0` and the other Bedrock ids, `gemini-*` to Vertex, Cohere
  and AI21 names) went to that provider with `OPENAI_API_KEY` as its key; Anthropic and
  Bedrock refuse it, so such a run worked only while `OPENAI_API_KEY` was unset. It now goes
  to the OpenAI endpoint like any other name litellm does not know as OpenAI's. To reach those
  clouds, use `--provider anthropic`, `amazon-bedrock` or `google-vertex`.

### Documentation

- **`docs/reviewing.md`: running against an OpenAI-compatible server** — provider `openai` with
  `OPENAI_API_BASE` / `OPENAI_BASE_URL`, which names reach the server and which keep a
  litellm provider prefix's route (write `openai/<name>` for those), a placeholder
  `OPENAI_API_KEY` for a server that ignores it, and why cost is unknown and
  `novelty --max-usd` stops after one call.
