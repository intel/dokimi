# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Run a consumer against an immutable delivery and grade its output in a fresh container."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
import shlex

from dokimi import docker_cmd, llm_cli, process
from dokimi.container_policy import ContainerPolicy
from dokimi.gate.delivery import compare_pairs, evaluate, score
from dokimi.utils import InputError, read_json_input_bytes


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def document(path: Path) -> tuple[dict, str]:
    raw, value = read_json_input_bytes(path)
    if not isinstance(value, dict):
        raise InputError(f"{path}: expected an object")
    return value, digest(raw)


def snapshot(base: Path, ref: dict) -> bytes:
    content = (base / ref["path"]).read_bytes()
    if digest(content) != ref["sha256"]:
        raise ValueError(f"digest mismatch: {ref['path']}")
    return content


def container(
    image: str,
    script: str,
    mounts: dict,
    data: bytes,
    timeout: int,
    gpu: bool = False,
    platform: str = "linux/amd64",
) -> process.Completed:
    name = "dokimi-delivery-" + uuid.uuid4().hex
    policy = ContainerPolicy(
        network="none", cap_drop_all=True, no_new_privileges=True, pids_limit=128
    )
    options = [
        "--rm",
        "--name",
        name,
        "--pull=never",
        "-i",
        "-w",
        "/consumer",
        "--entrypoint=/bin/sh",
    ]
    options.extend(["--platform", platform])
    if gpu:
        options.extend(["--gpus", "all"])
    mounts_argv = docker_cmd.mount_args((path, "/" + dest, True) for dest, path in mounts.items())
    spec = docker_cmd.run_spec(
        policy, [options, "network", "hardening", "pids", mounts_argv], image, ["-c", script]
    )
    env = process.Env.only(("PATH", "HOME", "DOCKER_HOST", "XDG_RUNTIME_DIR"))
    result = process.run(docker_cmd.argv(*spec), input=data, env=env, timeout=timeout)
    if result.timed_out:
        process.run(docker_cmd.argv("rm", "-f", name), env=env, timeout=15)
    if (
        result.timed_out
        or result.truncated
        or result.returncode in (125, 137)
        or (result.returncode in (126, 127) and result.stderr.startswith("docker:"))
    ):
        raise ValueError(result.stderr or "container timeout, killed process or truncated evidence")
    return result


def attempt(
    args: argparse.Namespace,
    manifest: dict,
    task: dict,
    sources: list[dict],
    arm: str,
    repeat: int,
    row: dict,
) -> str:
    root = args.tasks.parent
    supplied = []
    refs = []
    for base, ref in [
        (args.manifest.parent, manifest["docs"]),
        *((args.corpus.parent, s) for s in sources),
    ]:
        if ref.get("unavailable"):
            raise ValueError(f"{ref['url']}: {ref['unavailable']}")
        supplied.append(snapshot(base, ref).decode())
        refs.append(ref)
    image = manifest["image"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("runtime image needs its immutable local image ID")
    product = snapshot(args.manifest.parent, manifest["artifact"])
    verifier = snapshot(root, task["verifier"]).decode()
    hidden = snapshot(root, task["input"])
    initial = snapshot(root, task["initial"]).decode()
    row["verifier"] = task["verifier"]["sha256"]
    script = supplied[0] if task["example"] else task["contract"]
    if arm != "release":
        row["sources_supplied"] = refs
        if args.model:
            from dokimi.llm import llm_call_with_usage

            prompt = json.dumps(
                {"instruction": task["instruction"], "initial": initial, "sources": supplied}
            )
            row["model_calls"] = 1
            reply, usage = llm_call_with_usage(
                prompt,
                args.model,
                provider=args.provider,
                draw=repeat,
                system="Return a JSON object with script (POSIX shell, stdin to stdout) and claimed_success (boolean).",
            )
            row.update(
                resolved_model=usage.resolved_model or None,
                replayed=usage.replayed,
                cost_usd=0 if usage.replayed else usage.cost_usd,
            )
            try:
                answer = json.loads(reply)
            except ValueError:
                answer = None
        else:
            answer = task["fixture"].get(
                digest("\n".join(supplied).encode()), task["fixture"]["default"]
            )
        if not isinstance(answer, dict) or not isinstance(answer.get("script"), str):
            row["evidence"] = {"reason": "agent returned no executable answer"}
            return "FAIL"
        script = answer["script"]
        row["claimed_success"] = answer.get("claimed_success")
    budget = task["timeout"]
    if type(budget) is not int or budget <= 0:
        raise ValueError("timeout must be a positive integer")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "delivery"
        path.write_bytes(product)
        start = Path(tmp) / "initial"
        start.write_text(initial)
        result = container(
            image,
            "cp /initial ./consumer.sh || exit 121\n"
            + manifest["install"]
            + " || exit 120\n"
            + script,
            {"delivery": path, "initial": start},
            hidden,
            budget,
            manifest.get("gpu", False),
            manifest["platform"],
        )
    row["evidence"] = {
        "script": script,
        "exit": result.returncode,
        "stderr": result.stderr,
        "stdout": result.stdout,
        "verifier_exit": None,
    }
    grade = container(
        image,
        f"consumer_exit={result.returncode}\n" + verifier,
        {},
        result.stdout_bytes,
        budget,
        platform=manifest["platform"],
    )
    row["evidence"].update(verifier_exit=grade.returncode, verifier_stderr=grade.stderr)
    if grade.returncode not in (0, 10):
        raise ValueError(f"verifier failed with exit {grade.returncode}: {grade.stderr}")
    return "PASS" if grade.ok else "FAIL"


def collect(
    args: argparse.Namespace,
    manifest: dict,
    identity: dict,
    tasks: dict,
    required: list,
    arms: list,
    repeats: int,
    sources: list,
    mismatch: bool,
) -> list[dict]:
    observations = []
    for name in required:
        for arm in arms:
            for repeat in range(repeats):
                row: dict = {
                    "origin": "contract" if arm == "release" else "scan",
                    "identity": identity,
                    "task": name,
                    "arm": arm,
                    "repeat": repeat + 1,
                    "sources_supplied": [],
                    "observed_reads": None,
                    "evidence": None,
                    "outcome": "INCONCLUSIVE",
                    "reason": None,
                }
                try:
                    if mismatch:
                        raise ValueError("manifest or catalog does not match the release profile")
                    task = tasks[name]
                    row.update(
                        owner=task["owner"],
                        finding_id=task.get("finding_id"),
                        budget_seconds=task["timeout"],
                        verifier=task["verifier"]["sha256"],
                        input_sha256=task["input"]["sha256"],
                        initial_sha256=task["initial"]["sha256"],
                    )
                    row["outcome"] = attempt(
                        args, manifest, task, sources if arm == "B" else [], arm, repeat, row
                    )
                except Exception as exc:  # noqa: BLE001 - One unavailable runner, provider or invalid input is a missing measurement.
                    row["reason"] = f"{type(exc).__name__}: {exc}"
                observations.append(row)
    return observations


def run(args: argparse.Namespace) -> dict:
    manifest, manifest_id = document(args.manifest)
    catalog, catalog_id = document(args.tasks)
    profile = manifest["profile"]
    tasks = {name: task for name, task in catalog["tasks"].items() if task["profile"] == profile}
    identity = {
        "manifest": manifest_id,
        "artifact": manifest["artifact"]["sha256"],
        "profile": profile,
        "catalog": catalog_id,
        "image": manifest["image"],
        "version": manifest["version"],
        "platform": manifest["platform"],
        "docs_snapshot": manifest["docs"]["sha256"],
    }
    release = args.mode == "release"
    sources: list[dict] = []
    if release:
        policy, policy_id = document(args.policy)
        spec = policy["profiles"][profile]
        identity.update(policy=policy_id, policy_version=policy["version"])
        required = policy["profiles"][profile]["required"]
        if not isinstance(required, list) or len(set(required)) != len(required) or not required:
            raise InputError("policy needs distinct required checks")
        arms, repeats = ["release"], 1
    else:
        tasks = {name: task for name, task in tasks.items() if task.get("scan", True)}
        corpus, corpus_id = document(args.corpus)
        identity.update(
            corpus=corpus_id,
            harness="litellm-shell-v1" if args.model else "recorded-fixture",
            model=args.model,
            provider=args.provider if args.model else None,
        )
        if args.model and args.provider in llm_cli.BACKENDS:
            identity["harness_version"] = llm_cli.cli_version(args.provider) or None
        sources = corpus["sources"]
        if not isinstance(sources, list) or not all(
            isinstance(src, dict) and src.get("url") and src.get("captured_at") for src in sources
        ):
            raise InputError("corpus sources need a URL and capture time")
        required, repeats, arms = list(tasks), catalog["repeats"], ["A", "B"]
        weights = catalog["weights"][profile]
        if (
            not tasks
            or set(weights) != {t["group"] for t in tasks.values()}
            or type(repeats) is not int
            or repeats < 1
            or any(not math.isfinite(w) or w <= 0 for w in weights.values())
        ):
            raise InputError(
                "scan needs positive weights, applicable groups and at least one repeat"
            )
    mismatch = release and any(
        spec[field] != value
        for field, value in (
            ("product_version", manifest["version"]),
            ("image", manifest["image"]),
            ("platform", manifest["platform"]),
            ("tasks_sha256", catalog_id),
        )
    )
    observations = collect(
        args, manifest, identity, tasks, required, arms, repeats, sources, mismatch
    )
    if release:
        summary = evaluate(required, identity, observations)
    else:
        summary = {arm: score(weights, tasks, repeats, observations, arm) for arm in arms}
        summary["partial"] = any(summary[arm]["score"] is None for arm in arms)
        summary["paired"] = {
            name: compare_pairs(name, observations, repeats)
            for name in required
        }
        summary["attribution"] = {
            "exposure": "snapshot injection",
            "observed_reads": None,
            "influence": "unknown",
            "sources": sources,
            "evidence_quality": "recorded fixture" if not args.model else "one-turn paired replay",
        }
    argv = [
        "dokimi",
        "delivery",
        args.mode,
        "--manifest",
        str(args.manifest),
        "--tasks",
        str(args.tasks),
    ]
    argv += ["--policy", str(args.policy)] if release else ["--corpus", str(args.corpus)]
    if not release:
        argv += (
            ["--model", args.model, "--provider", args.provider] if args.model else ["--fixture"]
        )
    return {
        "required": required if release else [],
        "reproduce": shlex.join(argv + ["--out", "replay.json"]),
        "mode": args.mode,
        "date": datetime.now(timezone.utc).isoformat(),
        "identity": identity,
        "observations": observations,
        "summary": summary,
    }
