### Fixed

- **Built-in repository graders use Docker-visible storage.** Dokimi stages the grader alongside the exported eval directory. The Docker daemon needs access to their shared parent, so grading can run with Python installed on separate storage.
