### Changed

- A library id that cannot be resolved for an arm now logs a warning before the arm
  falls back to the product name, and a failed MCP tool listing or GitHub issue fetch
  is logged instead of dropped silently. JSON extraction from LLM replies catches only
  parse errors, so a bug in it is no longer read as "no JSON here".
