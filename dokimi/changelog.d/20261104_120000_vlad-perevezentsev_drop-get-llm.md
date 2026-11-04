### Removed

- **`dokimi.utils.get_llm`, `dokimi.llm.ChatOpenAI` and `dokimi.llm.ChatAnthropic`.** The judge,
  the persona and question generators and the question validator call
  `dokimi.llm.LLM` for every provider (#229 P1-2). Requests, cached replies and usage records do
  not change. If your code calls these names, use `dokimi.llm.Invoker(dokimi.llm.LLM(model, provider, api_key))`;
  it has the same `.invoke(prompt).content` shape.
