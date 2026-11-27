### Changed

- **`legacy-model-id` advice is now conditional: `if this text calls the model, use X`** — a line that documents a retirement or shows the old side of a migration is still reported, but the finding no longer tells you to replace an ID the text means to name. `docs/external-content.md` now says so and names `--ignore legacy-model-id` for a catalog that documents model history. (#945)
