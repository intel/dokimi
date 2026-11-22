### Added

- **`scripts/cli_spend_controls.py` lists the spend controls of every paid CLI command.** It prints, per paid leaf, whether it takes a dollar cap (`--max-usd`) and a preview (`--estimate-only`, `--dry-run`, `--work-dry-run`; `eval questions refine --dry-run` is left out because it still calls the model); `--json` ends with a count line. 28 of 39 paid leaves take no cap. `tests/test_cli_spend_controls.py` pins those 28, so a new paid leaf without a cap fails until it gets one or is listed. No command changes.
