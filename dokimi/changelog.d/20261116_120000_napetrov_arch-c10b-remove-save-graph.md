### Removed

- **`dokimi.novelty.needs.save_graph` is removed** — it wrote a need graph without the `schema_version` stamp
  and nothing in dokimi called it outside tests. `eval graph` writes `need_graph.v1` through the artifact
  writer, which stamps it. Code that imported `save_graph` should write the graph through `dokimi.artifacts`
  (`save_artifact("need_graph", graph.as_dict(), ...)`) instead. Runtime behaviour of the CLI is unchanged.
