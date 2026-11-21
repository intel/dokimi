### Fixed

- **`trace inspect` no longer shows a peak just under the window as 100% fill** — the `fill` column rounded the ratio to four places and then to a whole percent, so a peak of 198,999 in a 200,000-token window (99.4995%) showed as 100%. It is now rounded once, to 99%. `docs/context-window.md` now says `result chars` counts text only and skips non-text blocks such as images. (#962)
