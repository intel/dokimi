from pathlib import Path

_FRAGMENT = Path(__file__).resolve().parents[1] / "changelog.d" / "20261112_180000_napetrov_docker-argv-builders.md"


def test_removed_bullet_names_the_moved_docker_solver_names():
    text = _FRAGMENT.read_text(encoding="utf-8")
    for name in ("dokimi.harnesses.docker_solver.start_args",
                 "dokimi.harnesses.docker_solver.TASK_CONTAINER_HARDENING"):
        assert name in text, name


def test_removed_bullet_says_the_policy_replaces_the_flag_list():
    text = _FRAGMENT.read_text(encoding="utf-8")
    assert "task_container_policy(network=, cpus=, memory=)" in text
    assert "returns a `ContainerPolicy` rather than a flag list" in text
