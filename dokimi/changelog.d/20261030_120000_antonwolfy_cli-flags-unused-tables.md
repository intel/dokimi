### Removed

- **`dokimi.cli_flags.CANONICAL` and `dokimi.cli_flags.BANNED`** — the two module-level
  lookup tables (spelling to concept name) are gone, with the `_index()` helper that built
  them. Nothing read them: the flag contract test builds its own index from `CONCEPTS`. No
  command, flag or output changes.
