### Removed

- **Docker builder names moved** — `dokimi.repos.container.PIDS_LIMIT`, `dokimi.repos.container.ContainerPolicy` and `dokimi.harnesses.container_agent.ContainerPolicy` are gone; import the policy and the limit from `dokimi.container_policy`. `ContainerSandbox.run_args()` no longer returns `--network`; the task container takes it from its policy. The `docker run` argv the package builds is unchanged.
