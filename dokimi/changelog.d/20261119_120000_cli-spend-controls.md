### Added

- **`scripts/cli_spend_controls.py` lists the spend controls of every paid CLI command.** It prints, per paid leaf, whether it takes a dollar cap (`--max-usd`) and a preview (`--estimate-only`, `--dry-run`; `eval questions refine --dry-run` and `subjects run --work-dry-run` are left out because they still call the model); `--json` ends with a count line. 28 of 39 paid leaves take no cap. `tests/test_cli_spend_controls.py` pins those 28, so a new paid leaf without a cap fails until it gets one or is listed. No command changes.
