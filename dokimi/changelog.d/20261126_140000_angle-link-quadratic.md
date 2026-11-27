### Fixed

- **`dokimi memory lint` no longer takes quadratic time on a line with a link before many code spans** — for each code span after a `](`, the check for an `](<...>)` destination copied the paragraph and looked back to its start for the link's opener, so 80,000 characters of `[x](<y>) ` followed by `` `a` `` repeated took 0.9 s and doubling the line made it 3.5 times slower. The opener and the link are now looked up once per `](`, so for the `[x](<y>) ` shape the time grows in step with the line; a `[x]( ` followed by many code spans is still quadratic. The findings are unchanged.
