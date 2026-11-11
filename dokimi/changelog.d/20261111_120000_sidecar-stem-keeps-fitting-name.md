### Fixed

- **A `--out` stem that fits its partial-result name keeps it** — when `dokimi novelty routing --max-usd` stops a run and `--out` holds a finished result, the partial result goes to `<out stem>.partial.json` unchanged whenever that name fits the file system's name limit; a stem is cut and given a digest only for a name the counter makes too long, and a small name limit (16 to 31 bytes) saves a short `--out` such as `abc.json` as `abc.partial.json` instead of failing with "File name too long".
