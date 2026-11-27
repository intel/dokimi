### Fixed

- **A `Don't ever <verb>` question is no longer accepted as the reversal of a `Do not` statement** — `check_wording("reversal", ...)` read the adverb in `Don't ever run the installer as root?` as the verb, found it absent from the source and stripped the leading `Don't`, so the prohibition read as a flipped polarity. The adverbs `ever`, `always`, `just`, `still`, `also`, `even`, `really`, `actually`, `simply` and `again` are now skipped before the verb is compared, and the candidate is rejected as `polarity_not_flipped`.
