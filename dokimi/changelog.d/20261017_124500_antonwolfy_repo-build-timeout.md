### Added

- **`repo run --build-timeout` and `repo tasks check --build-timeout`** — the seconds the image build may run before it fails as a container error, 10800 (3 hours) by default as before. A toolchain image that legitimately takes longer could not be built at all: the image tag is the eval tree's digest, so a retry rebuilt from scratch and hit the same cap. `DockerRunner.build` takes the bound as `timeout_s`.
