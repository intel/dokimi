### Fixed

- **The judge, persona and question generators and the question validator route and key like the
  answerer** — on Bedrock, Vertex and OpenRouter they used a provider table of their own, and it
  differed from the answerer's:
  - A bare Bedrock name off the profile table (`claude-sonnet-4-6`) was replaced by
    `$ANTHROPIC_DEFAULT_SONNET_MODEL`, so the judge ran a model other than the one its record
    names. Bedrock runs no such name. As a judge, `arms run`, `arms matrix-run`, `subjects run`,
    `evaluate` and `benchmark run`/`batch` now refuse it with exit 2 before any answer is paid for,
    and the error names the fix: the inference profile id (`us.anthropic.claude-sonnet-4-6`) or an
    ARN. The commands that judge stored answers send it as given, and every judge call fails; with
    `--judge-temperature`, `eval pairwise`/`checklist`/`judge-wording`/`cross-judge` and `eval
    judge-validity --mode pairwise` refuse it with exit 2 first, as a temperature litellm does not
    list for it. The persona and question generators and the validator send it as given.
  - An explicit `api_key` was ignored on Bedrock: only `AWS_BEARER_TOKEN_BEDROCK` was read. It now
    wins, as on every other provider.
  - An unreadable `file:` key was sent as the literal `file:...` text. It now raises
    `Cannot read API key from file ...` before any request.
  - OpenRouter output was capped at 2000 tokens. It now has no ceiling of its own, as the answerer
    there never did.
  - A model name that starts with a litellm route passes through, as for the answerer: on
    `google`/`google-vertex`, `gemini/gemini-2.5-pro` goes to Google AI Studio instead of
    `vertex_ai/gemini/...`, and on Bedrock `bedrock/...` is no longer doubled to `bedrock/bedrock/...`.
    A Vertex Model Garden name (`meta/llama-3.1-405b-instruct-maas`) keeps its `vertex_ai/` route;
    the answerer sent it without one, which litellm cannot place, and now adds it too.
  - The `vertex_ai` provider name sent no key. It now reads `GEMINI_API_KEY`, as `google-vertex`
    does. The answerer sent it `OPENAI_API_KEY`, and now reads `GEMINI_API_KEY` too.
  - The answerer routed a Bedrock ARN without its `bedrock/` prefix, so litellm could not place
    it. It now gets the prefix, as the judge always did.
  - On Bedrock, a `/` name with another vendor's route (`gemini/...`, `openai/...`) gets
    `bedrock/` too, so `AWS_BEARER_TOKEN_BEDROCK` goes only to Bedrock. The answerer sent it to
    that vendor's route, with the Bedrock token as its key.

  Response-cache entries these four stored on Bedrock, Vertex or OpenRouter are not hit, because
  the key now includes the streaming flag, as every other caller's does. Those calls are paid once
  more. Usage records name the provider and model, not `litellm` and the route.
