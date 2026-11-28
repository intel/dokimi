### Fixed

- **`repo security` reads a line of repeated `rm -r` or `rm -rf` in linear time.** The `destructive-delete` rule scanned the rest of the line from every `rm`, so `rm -rf ` repeated 3000 times took 0.64 s. It now reads the words after the first `rm` of a segment that has a recursive flag once, and finds the same lines (#688).
