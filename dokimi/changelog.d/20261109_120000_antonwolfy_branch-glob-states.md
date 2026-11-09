### Fixed

- **`repo scan` no longer walks a whole branch filter for each character of a claim** — matching
  a claim against a `branches` / `branches-ignore` filter read every atom of the filter for every
  character of the claim, and went on to the claim's end after the filter had stopped matching
  it, so a filter and claim of 2,000 characters took millions of steps. The walk now passes over
  only the optional atoms (`?`, `*`) that follow the atoms the claim can be at, and stops at the
  first character the filter does not take. What a filter matches is unchanged.
