### Added

- **Signed releases with build provenance** — the release workflow signs the
  sdist and wheel with Sigstore (keyless, the workflow's identity) and attaches
  `<file>.sigstore.json` bundles plus a SLSA build-provenance attestation
  (`dokimi-X.Y.Z.intoto.jsonl`, also in `gh attestation verify`) to the GitHub
  Release. `docs/releasing.md` shows how to verify a download.
