### Changed

- **Bad input exits 2 in the `novelty` and `report model-compare` commands** — a
  file that `novelty` cannot read or that is not the artifact it names (`--artifact`,
  `--atoms`, `--probes`, `--competitors`, `--queries-by-skill`), and the runs that
  `report model-compare` refuses (a missing, unreadable, non-JSON or non-object file;
  runs that disagree on the treatment, prompt or judge), and the skill fields that
  `novelty routing` refuses (a missing competitor, a skill listed twice, a skill compared
  with itself), now exit 2 instead of 1, with `Error: <message>` on stderr, where the
  message names the file and the reason. Exit 1 stays the verdict of the gates
  (`--strict`, `eval judge-validity`), so a script that branches on the code must
  treat 2 as "fix the input". The same exit and `Error:` line now come from any input
  file the loaders reject. Two `report model-compare` messages changed wording:
  `File not found: <file>` is now `cannot read <file>: No such file or directory`, and
  `Invalid JSON in <file>` is now `<file> is not valid JSON`. An invalid artifact named by
  `novelty view` says which part is wrong: `is not valid JSON`, `is not UTF-8 text`,
  `is not a novelty artifact: expected a JSON object`, or `is not a valid novelty artifact:
  <reason>`.
