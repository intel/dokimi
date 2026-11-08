### Fixed

- **`--provider openai-codex` is no longer offered where the run refuses it** —
  `answers generate`, `eval questions generate`, `eval personas discover`,
  `evaluate` and `benchmark run|preflight|batch` listed it, but their answerer
  and generators do not take it: the run stopped on a `ValueError: Unsupported
  provider` traceback (exit 1), `evaluate` after making its output directories,
  and `benchmark preflight` passed a plan `benchmark run` refuses. argparse now
  refuses it (exit 2) before anything runs. The commands that call the model
  through `dokimi.llm` directly (`arms`, `subjects run`, `skill novelty`,
  `skill shorten`, `novelty routing`, `eval questions analyze|review`,
  `eval score panel`, `tools preference`) keep it.
