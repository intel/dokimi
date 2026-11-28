### Fixed

- **`install-unpinned` exempts a bare `npx` of the skill's own package or declared bin** — the exemption assumes the example starts in the skill's folder. After `cd`, `pushd`, or `popd`, the audit checks later commands in the same example as usual. Explicit package selections, version tags and `npx` invocations that set the project or workspace are still checked. The audit reads the `package.json` beside `SKILL.md` for bundled skills. At the scanned checkout root, it reads only `SKILL.md`.
