### Internal

- **CLI flag registry and contract test** — `dokimi/cli_flags.py` lists each flag concept with its canonical spelling, type and banned synonyms. `tests/test_cli_flags.py` fails a flag that is neither canonical nor on `tests/data/cli_flags_allowlist.txt`, and a banned synonym, with the canonical spelling in the message. `docs/reference/cli-interface.json` is the #88 surface snapshot, regenerated with `make cli-snapshot`. No command reads the registry yet; no flag, default or output changes.
