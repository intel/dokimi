### Fixed

- **`bundle-file-unreferenced` no longer counts a translation of `SKILL.md`** — a localized copy at the skill root, such as `SKILL.ru.md` or `SKILL.zh-CN.md`, holds the same instructions as `SKILL.md`, so it is not a bundled file the agent is left to find. It is still read as a document, so the files it names count as named. `SKILL.notes.md` and `notes.ru.md` are still counted; a translation in a subfolder is a nested skill's file and is unaffected. Nothing to do for authors: the warning goes away for the translations; a real unnamed script or document still gets it.
