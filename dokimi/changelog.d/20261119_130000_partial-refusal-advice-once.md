### Fixed

- **A partial atoms file's refusal no longer repeats the advice its message already ends with** — `load_atoms_record` appended `Drop --max-usd to run unmetered` (or `Fix the error and rerun`) after the stored `message` of a `no_price` or `permanent_error` stop, and a real `no_price` message already ends with it, so the refusal said it twice. A message that ends with the reason's advice (ignoring trailing periods and whitespace) now reads `<message>; it is not a finished atom set.`; every other message is unchanged. (#921)
