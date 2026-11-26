### Fixed

- **`install-unpinned` no longer counts `npx` of the skill's own bin** — `npx <name>` where `<name>` is the package name or a `bin` entry of the `package.json` beside `SKILL.md` runs the skill's own code, not a registry install, so it is not reported. `npx mytool@latest` is still reported (the tag asks the registry), and so is `npx otherbin` when the package declares no such bin. A `package.json` in a subfolder, or one that is too big or not JSON, is not read.
