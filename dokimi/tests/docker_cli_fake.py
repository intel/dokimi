# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A :class:`~dokimi.process.FakeRunner` standing for the docker CLI and the solver.

The ``docker-*`` harnesses start every process through their ``runner``; give
them a :class:`DockerCLI` (``create_harness(id, runner=cli)``) and nothing is
started. It answers like a daemon with one healthy task container:

- every docker step exits 0 with no output;
- ``docker logs`` of the egress sidecar says the proxy is ready, then
  ``sidecar_log``;
- ``docker cp <c>:/logs/verifier/reward.txt <dst>`` writes ``reward`` to
  ``<dst>`` (``None``: no reward file, exit 1);
- ``docker inspect -f {{.State.Running}}`` says ``true``;
- the solver, ``claude -p`` on the host or ``docker exec ... claude -p`` in the
  container, prints ``solver`` (a stream-json string), or answers through
  ``solver`` when it is a callable taking the :class:`~dokimi.process.Call`.

``answer(call)`` is asked first and overrides any of that when it returns a
:class:`~dokimi.process.Completed`. A program named in ``real`` (``sh``, for the
host isolation probe) is run for real, with the call's argv, env and cwd.
``args`` lists each docker call's argv after ``docker``, in order (what tests
used to record from ``_docker``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Union

from dokimi import process
from dokimi.harnesses.container_agent import AGENT_BIN
from dokimi.process import Call, Completed, FakeRunner, reply

READY = '{"ready": true, "port": 3128}\n'

Answer = Callable[[Call], Optional[Completed]]


class DockerCLI(FakeRunner):
    def __init__(self, *, reward: Optional[str] = "1.0", solver: Union[str, Answer] = "",
                 sidecar_log: str = "", answer: Optional[Answer] = None, real: tuple[str, ...] = ()):
        super().__init__(self._handle)
        self.reward, self.solver, self.sidecar_log, self.answer = reward, solver, sidecar_log, answer
        self.real = real
        self.args: list[list[str]] = []

    @staticmethod
    def is_solver(argv: tuple[str, ...]) -> bool:
        if argv[0] == "docker":
            return argv[1] == "exec" and AGENT_BIN in argv and "-p" in argv
        return Path(argv[0]).name == "claude"

    @staticmethod
    def is_probe(argv: tuple[str, ...]) -> bool:
        """The in-container isolation probe (``docker exec ... sh -c <probe>``)."""
        return argv[:2] == ("docker", "exec") and "--env-file" in argv and argv[-2] == "-c"

    def _handle(self, call: Call) -> Optional[Completed]:
        if call.argv[0] == "docker":
            self.args.append(list(call.argv[1:]))
        if self.answer is not None:
            got = self.answer(call)
            if got is not None:
                return got
        if Path(call.argv[0]).name in self.real:
            return process.run(list(call.argv), timeout=call.timeout, env=process.Env.only((), call.env),
                               cwd=call.cwd, input=call.input)
        if self.is_solver(call.argv):
            return self.solver(call) if callable(self.solver) else reply(self.solver)
        if call.argv[0] != "docker":
            return None
        args = call.argv[1:]
        if args[0] == "logs":
            return reply(READY + self.sidecar_log)
        if args[0] == "cp" and args[1].endswith(":/logs/verifier/reward.txt"):
            if self.reward is None:
                return reply(stderr="Error: No such container:path", returncode=1)
            Path(args[2]).write_text(self.reward, encoding="utf-8")
        if args[0] == "inspect" and "{{.State.Running}}" in args:
            return reply("true\n")
        return None

    def solver_calls(self) -> list[Call]:
        return [c for c in self.calls if self.is_solver(c.argv)]
