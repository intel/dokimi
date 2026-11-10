### Fixed

- **A `guidance:rev:` arm's guidance history keys a file whose path is not valid UTF-8 as the arm lists it** — the history decoded a path with replacement, so `guidance_latest` left out the file and the date-based leak check reported clean for guidance committed after the task's `public_at`. A path is now decoded as a file name, as the rest of the `-z` listings are; a commit subject stays text.
