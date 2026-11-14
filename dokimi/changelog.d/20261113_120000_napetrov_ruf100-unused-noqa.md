### Changed

- **ruff now flags a stale `# noqa`** — RUF100 is selected, and the four `# noqa: F401` comments that silenced
  nothing are removed. Codes of rules dokimi does not select (BLE001, E402, E731 and a few more) are listed in
  `lint.external`, so their `noqa` comments stay. No runtime change.
