# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run a product-control replay or a deterministic skill fixture through the CLI."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


PROJECT = Path(__file__).resolve().parents[1]
SUITE_VERSION = "product-experiment.v1"
TASK = "A developer needs to train a linear regression model and make predictions."
CRITERIA = "Use CPU. Predictions must match the reference on held-out data."
PRODUCT = "sklearnex"


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def build_suite(task, criteria, product=None):
    openings = {
        "ask": ("Recommend an approach.", "What approach would you recommend?",
                "Which approach should a developer choose?"),
        "execute": ("Implement the solution.", "Make the implementation changes.",
                    "Write the code that solves the task."),
    }
    if product and product.casefold() in f"{task}\n{criteria}".casefold():
        raise ValueError("The shared task and criteria must be product-neutral")
    cases = []
    for mode, variants in openings.items():
        for cue in ("free", "named") if product else ("free",):
            direction = ""
            if cue == "named":
                direction = (f"Consider {product} in your recommendation.\n" if mode == "ask"
                             else f"Use {product} in your implementation.\n")
            for index, opening in enumerate(variants):
                prompt = f"{opening}\n{direction}{task}\n{criteria}"
                if cue == "free" and product and product.casefold() in prompt.casefold():
                    raise ValueError("Product name leaked into a free prompt")
                cases.append({"id": f"{mode}-{cue}-{index}", "mode": mode, "cue": cue,
                              "prompt": prompt})
    return {"version": SUITE_VERSION, "cases": cases}


def example_controls():
    rows = []
    for name, backend in (("late_patch", "not_observed"), ("stock", "not_observed"),
                          ("positive_patched", "accelerated")):
        for seed in range(1000, 1003):
            rows.append({"control": name, "seed": seed, "expected": {"quality_ok": True},
                         "got": {"quality_ok": True, "predict": backend}, "pass": True})
    return rows


def check_control(row):
    observed = row["got"]
    if not isinstance(observed["quality_ok"], bool) or observed["predict"] not in {
        "accelerated", "fallback", "not_observed",
    }:
        raise ValueError("Unavailable evaluator verdict")
    if not row["pass"] or any(observed[key] != value for key, value in row["expected"].items()):
        raise ValueError("The stored evaluator result did not pass its control assertion")
    return {"recommended": None, "attempted": None, "fit_backend": observed.get("fit"),
            "predict_backend": observed["predict"], "correct": observed["quality_ok"]}


def fixture_agent(skill_text):
    if "casefold" in skill_text:
        return str.casefold
    return str.lower


def evaluate_control(control, settings, output):
    root = Path(settings["root"])
    python = settings["python"]
    evaluation = output / "evaluation.json"
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory) / "workspace"
        shutil.copytree(root / "starter", workspace)
        hashes = {file.relative_to(workspace).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
                  for file in workspace.rglob("*") if file.is_file()}
        shutil.copytree(root / "controls" / control["control"], workspace, dirs_exist_ok=True)
        scenario = Path(directory) / "scenario.json"
        original = Path(directory) / "original.json"
        write_json(scenario, {"family": control["family"]})
        write_json(original, hashes)
        subprocess.run([python, str(root / "evaluator/verify.py"), "--workspace", str(workspace),
                        "--scenario", str(scenario), "--python", python, "--scoring-python", python,
                        "--out", str(evaluation), "--seed", str(control["seed"]),
                        "--original-hashes", str(original)], check=True)
    result = json.loads(evaluation.read_text(encoding="utf-8"))
    if result["evaluator_status"] != "ok":
        raise ValueError(f"Evaluator infrastructure error: {result['evaluator_errors']}")
    use = result["use"]
    integrity = result["integrity"]
    if not integrity["evaluator_unchanged"] or integrity["site_tamper_flags"]:
        raise ValueError("Evaluator integrity check failed")
    products = result["selection"]["products"]
    return {"recommended": None, "attempted": "scikit-learn-intelex" in products,
            "sklearnex_loaded": use["sklearnex_loaded_train"], "correct": result["quality_ok"],
            "fit_backend": use["fit"], "predict_backend": use["predict"],
            "constraint_ok": result["constraint_ok"]}


def run_cell(args):
    output = Path(os.environ["DOKIMI_OUTPUT_DIR"])
    reward = output / "reward.txt"
    # An unreadable reward means no verifier verdict to the command harness.
    reward.write_text("unavailable\n", encoding="utf-8")
    case = json.loads((args.cell / "case.json").read_text(encoding="utf-8"))
    if args.arm in ("with-skill", "without-skill"):
        skill = args.cell / "SKILL.md"
        solution = fixture_agent(skill.read_text(encoding="utf-8") if args.arm == "with-skill" else "")
        correct = [solution(word) for word in ("Straße", "MÜNCHEN")] == ["strasse", "münchen"]
        verdicts = {"correct": correct, "skill_supplied": args.arm == "with-skill"}
        passed = correct
    else:
        repeat = int(output.name.removeprefix("run"))
        control = case["controls"][repeat - 1]
        if "evaluator" in case:
            verdicts = evaluate_control(control, case["evaluator"], output)
        else:
            verdicts = check_control(control)
        passed = verdicts["correct"] and verdicts.get("constraint_ok", True) is not False and (
            args.arm == "correctness" or verdicts["predict_backend"] == "accelerated"
        )
    write_json(output / "evidence.json", {"source": case["source"], "verdicts": verdicts})
    reward.write_text(f"{int(passed)}\n", encoding="utf-8")


def materialize_tasks(args, root, prompts):
    source = "test_fixture"
    if args.skill_fixture:
        inputs = {"unicode-key": {"prompt": prompts["unicode-key"], "source": source}}
        arms = ["without-skill", "with-skill"]
    else:
        rows = example_controls()
        if args.controls:
            raw = args.controls.read_bytes()
            stored = json.loads(raw)
            rows = stored["controls"]
            write_json(root / "replay-input.json", {"sha256": hashlib.sha256(raw).hexdigest(),
                                                   "revision": stored["revision"]})
            source = "replay"
        if "execute-named-0" not in prompts:
            raise ValueError("The suite has no product-named cases; replay needs a suite "
                             "generated with a product")
        inputs = {}
        for name, cue in (("late_patch", "named"), ("stock", "free"),
                          ("positive_patched", "named")):
            controls = [row for row in rows if row["control"] == name]
            if len(controls) != 3 or len({row["seed"] for row in controls}) != 3:
                raise ValueError(f"{name} must have three distinct seeds")
            case = {
                "source": "test_fixture" if args.evaluator_root else source,
                "prompt": prompts[f"execute-{cue}-0"],
                "controls": sorted(controls, key=lambda row: row["seed"]),
            }
            if args.evaluator_root:
                case["evaluator"] = {"root": str(args.evaluator_root.resolve()),
                                     "python": str(args.evaluator_python.absolute())}
            inputs[name.replace("_", "-")] = case
        arms = ["correctness", "correctness-and-backend"]
    for name, case in inputs.items():
        task = root / "tasks" / name
        task.mkdir(parents=True)
        (task / "task.toml").write_text('version = "1.0"\n', encoding="utf-8")
        (task / "instruction.md").write_text(case["prompt"] + "\n", encoding="utf-8")
        write_json(task / "case.json", case)
        if args.skill_fixture:
            skill_text = (
                "---\nname: unicode-keys\ndescription: Normalize Unicode keys for matching.\n"
                "---\nUse Python's casefold for caseless matching.\n"
            )
            if args.skill_file:
                skill_text = args.skill_file.read_text(encoding="utf-8")
            (task / "SKILL.md").write_text(skill_text, encoding="utf-8")
    return inputs, arms


def run(args):
    root = args.out.resolve()
    root.mkdir(parents=True)
    if args.suite:
        (root / "suite.json").write_bytes(args.suite.read_bytes())
    elif args.skill_fixture:
        write_json(root / "suite.json", {"version": "unicode-keys.v1", "cases": [{
            "id": "unicode-key", "mode": "execute",
            "prompt": "Implement Unicode caseless matching for keys.",
        }]})
    else:
        write_json(root / "suite.json", build_suite(TASK, CRITERIA, PRODUCT))
    suite_bytes = (root / "suite.json").read_bytes()
    suite = json.loads(suite_bytes)
    suite_sha = hashlib.sha256(suite_bytes).hexdigest()
    prompts = {case["id"]: case["prompt"] for case in suite["cases"]}
    inputs, arms = materialize_tasks(args, root, prompts)
    runs = root / "runs.json"
    template = f"{shlex.join([sys.executable, __file__])} --cell '{{task_path}}' --arm '{{harness}}'"
    process = subprocess.run(
        [sys.executable, "cli.py", "tasks", "run", "--all", "--tasks-root", str(root / "tasks"),
         "--harnesses", ",".join(arms), "--reps", "3", "--baseline-harness", arms[0],
         "--out", str(runs), "--out-dir", str(root / "cells"), "--command-template", template],
        cwd=PROJECT, check=False,
    )
    if not runs.is_file():
        process.check_returncode()
    cells = []
    for row in json.loads(runs.read_text(encoding="utf-8"))["results"]:
        case = inputs[row["task"]]
        evidence = Path(row["output_dir"]) / "evidence.json"
        error = row["metrics"].get("error") or row["metrics"].get("solver_error")
        relative_evidence = evidence.relative_to(root).as_posix() if evidence.exists() else None
        controls = case.get("controls")
        control = controls[row["repeat"] - 1] if controls else None
        evaluation = Path(row["output_dir"]) / "evaluation.json"
        cells.append({"source": case["source"], "mode": "execute", "suite_prompt": case["prompt"],
                      "original_prompt": None if controls else case["prompt"],
                      "task": row["task"], "arm": row["harness"], "repeat": row["repeat"],
                      "seed": control["seed"] if control else None,
                      "original_environment": control.get("versions")
                      if case["source"] == "replay" else None,
                      "status": "infrastructure_error" if error else
                                ("passed" if row["passed"] else "task_failed"),
                      "error": error, "elapsed_seconds": row["elapsed_sec"],
                      "verdicts": json.loads(evidence.read_text(encoding="utf-8"))["verdicts"]
                      if relative_evidence else None, "evidence": relative_evidence,
                      "evaluator_evidence": evaluation.relative_to(root).as_posix()
                      if evaluation.exists() else None})
    write_json(root / "response.json", {
        "suite_version": suite["version"],
        "suite_sha256": suite_sha,
        "agent": "python-fixture" if args.skill_fixture else None, "model": None,
        "environment": {"runner": "local command harness", "python": sys.version.split()[0]},
        "repeats": 3, "model_cost_usd": 0.0, "original_cost_usd": None,
        "dokimi_artifact": "runs.json", "comparison": "runs.md", "cells": cells,
    })
    return process.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="New directory for generated suite and results")
    variant = parser.add_mutually_exclusive_group()
    variant.add_argument("--controls", type=Path, help="Stored evaluator control results")
    variant.add_argument("--skill-fixture", action="store_true")
    variant.add_argument("--generate-suite", type=Path, help="Build a frozen suite from a JSON request")
    parser.add_argument("--suite", type=Path, help="Read a previously frozen suite byte for byte")
    parser.add_argument("--skill-file", type=Path, help="Replace SKILL.md in the skill fixture")
    parser.add_argument("--evaluator-root", type=Path, help="Use a trusted external evaluator on the controls")
    parser.add_argument("--evaluator-python", type=Path, help="Python with the evaluator's dependencies")
    parser.add_argument("--cell", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--arm", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.skill_file and not args.skill_fixture:
        parser.error("--skill-file requires --skill-fixture")
    if args.evaluator_root or args.evaluator_python:
        if not (args.controls and args.evaluator_root and args.evaluator_python):
            parser.error("Fresh evaluation requires --controls, --evaluator-root and --evaluator-python")
    if args.cell:
        run_cell(args)
    elif args.out and args.generate_suite:
        request = json.loads(args.generate_suite.read_text(encoding="utf-8"))
        criteria = "\n".join([*request["constraints"], *request["success_criteria"]])
        suite = build_suite(request["task"], criteria, request.get("product"))
        args.out.mkdir(parents=True)
        write_json(args.out / "suite.json", suite)
    elif args.out:
        return run(args)
    else:
        parser.error("--out is required")


if __name__ == "__main__":
    sys.exit(main())
