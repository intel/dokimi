### Fixed

- **`catalog check` no longer opens a `SKILL.md` that a `skills.yaml` entry links out of the root.** The entry was refused as `SKILL.md resolves outside root` only after the linked file had been read and parsed; the containment check now runs first, so the outside file is never opened. A `SKILL.md` linked to another file inside the root still loads. ([#1126](https://github.com/intel/dokimi/issues/1126))
