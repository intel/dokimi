### Fixed

- **A scorecard ref with a non-UTF-8 name no longer stops the digest** — `_digest_ref` and the digests of the question set and of an arm spec raised `UnicodeEncodeError` on a ref that is no path and holds a lone surrogate (a surrogate-escaped name). They now hash the name's bytes (`os.fsencode`); the digest of a UTF-8 ref is unchanged.
