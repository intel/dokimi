### Fixed

- **A partial atoms file with no usable message now says why the run stopped** — for a `no_price` or `permanent_error` stop, the refusal of `load_atoms_record` printed the block's `message` as it was, so a block with no message gave an empty slot (`is a partial atoms file: , so it ...`) and a blank, zero-width or non-text message gave junk. It now names the stop (`a call with no known price stopped the run`, `a permanent provider error stopped the run`) when the message is not text with a visible character; a real message is printed as before. (#833)
