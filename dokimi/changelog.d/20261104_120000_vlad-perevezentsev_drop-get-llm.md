### Removed

- **`dokimi.utils.get_llm`, `dokimi.utils.bedrock_litellm_model`, `dokimi.llm.ChatOpenAI` and
  `dokimi.llm.ChatAnthropic`.** The answerer, judge, persona and question generators and question
  validator call `dokimi.llm.LLM` for every provider; their requests, cached replies and usage
  records do not change (#229 P1-2). `ChatOpenAI(model=m, api_key=k)` becomes
  `dokimi.llm.Invoker(dokimi.llm.LLM(m, "openai", k))` (`"anthropic"` for `ChatAnthropic`), which
  sends the same request. A direct `get_llm(provider, m)` call becomes the same line, but the route
  and key then follow `dokimi.llm`'s table: an unknown bare Bedrock name is no longer replaced by
  `$ANTHROPIC_DEFAULT_SONNET_MODEL`, OpenRouter has no 2000-token cap, Bedrock uses the key it is
  given, usage records the model and provider instead of the route and `litellm`, and replies
  `get_llm` cached are not found. In place of `bedrock_litellm_model(m)`,
  `dokimi.llm.resolve_model(m, "amazon-bedrock")` gives the route under the same table.
