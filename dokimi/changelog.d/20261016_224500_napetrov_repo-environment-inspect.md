### Added

- **`dokimi repo inspect`** — a static look at what a repository offers a
  coding agent, with no manifest, LLM call or repository command: instruction
  files per harness, build/test/lint/typecheck surfaces per project root,
  MCP/hook/plugin capabilities, risky `package.json` scripts, automatic hooks
  that run repository code, and generated, test and large files. Writes the new
  `repo_environment.v1` artifact; `--fail-on-error` makes it a CI gate.
