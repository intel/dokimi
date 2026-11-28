# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Create a standalone toy supplier and consumer pack. Takes a local Docker image ID."""

import hashlib
import json
import sys
from pathlib import Path


def build(root: Path, image: str) -> None:
    root.mkdir(parents=True, exist_ok=True)

    def save(name, content):
        (root / name).write_bytes(content.encode())
        return {"path": name, "sha256": hashlib.sha256(content.encode()).hexdigest()}

    def write(name, obj):
        save(name + ".json", json.dumps(obj, indent=2))

    product = """#!/bin/sh
case "$1" in
  double|twice) awk '{print "cpu:" 2*$1}' ;;
  --legacy) awk '{print "fallback:" 2*$1}' ;;
  *) exit 64 ;;
esac
"""
    current = "shipcalc double"
    old = "shipcalc --legacy"
    docs = save("shipped-example.sh", current)
    base = {
        "platform": "linux/amd64",
        "profile": "linux-cpu",
        "version": "2.0-rc1",
        "docs": docs,
        "image": image,
        "install": 'install -m 755 /delivery ./shipcalc && export PATH="$PWD:$PATH"',
    }
    for name, code in [("good", product), ("broken", product.replace("double|twice)", "twice)"))]:
        write(name, dict(base, artifact=save(name + ".cli", code)))
    write(
        "bad-example",
        dict(base, artifact=save("good.cli", product), docs=save("bad-example.sh", old)),
    )
    guide = dict(
        save("article.txt", old),
        url="https://example.invalid/v1/tutorial",
        version="1",
        captured_at="2026-11-22T00:00:00Z",
        owner="article author",
        recommendation="Label syntax as v1",
        finding_id="article-v1",
        conflict="v1 selects fallback on v2",
    )
    write("corpus", {"sources": [guide]})
    task = {
        "profile": "linux-cpu",
        "group": "operations",
        "timeout": 20,
        "example": True,
        "contract": current,
        "instruction": "Complete the shell consumer to double each input number with the cpu backend.",
        "initial": save("consumer.sh", "#!/bin/sh\n# Read numbers on stdin.\n"),
        "input": save("hidden.txt", "17\n-4\n"),
        "verifier": save(
            "verify.sh",
            """[ "$consumer_exit" = 0 ] || exit 10
expected=$(printf 'cpu:34\\ncpu:-8') || exit 2
actual=$(cat) || exit 2
[ "$expected" = "$actual" ] || exit 10\n""",
        ),
        "owner": "shipped example",
        "fixture": {
            "default": {"script": current, "claimed_success": True},
            hashlib.sha256((current + "\n" + old).encode()).hexdigest(): {
                "script": old,
                "claimed_success": True,
            },
        },
    }
    operation = dict(task, example=False, scan=False, owner="product CLI")
    catalog = {
        "tasks": {"double": task, "binary-double": operation},
        "repeats": 2,
        "weights": {"linux-cpu": {"operations": 1}},
    }
    write("tasks", catalog)
    profile = {
        "required": ["double", "binary-double"],
        "product_version": base["version"],
        "image": image,
        "platform": base["platform"],
        "tasks_sha256": hashlib.sha256((root / "tasks.json").read_bytes()).hexdigest(),
    }
    write("policy", {"version": "1", "profiles": {"linux-cpu": profile}})

    finding = "missing-zero-row"
    zero_bug = product.replace('{print "cpu:"', '$1 != 0 {print "cpu:"')
    write("zero-rc", dict(base, artifact=save("zero.cli", zero_bug)))
    source = dict(
        save("zero-observation.txt", "The v2 RC drops the output row for zero."),
        url="https://example.invalid/v2/zero",
        captured_at="2026-11-22T00:00:00Z",
        finding_id=finding,
        owner="product CLI",
        recommendation="Return a result for every input row",
    )
    write("zero-corpus", {"sources": [source]})
    zero = dict(
        operation,
        scan=True,
        finding_id=finding,
        input=save("zero.txt", "0\n"),
        verifier=save(
            "verify-zero.sh",
            """value=$(cat) || exit 2
[ "$consumer_exit" = 0 ] && [ "$value" = cpu:0 ] || exit 10\n""",
        ),
    )
    catalog["tasks"]["double-zero"] = zero
    write("regression-tasks", catalog)
    profile = dict(
        profile,
        tasks_sha256=hashlib.sha256((root / "regression-tasks.json").read_bytes()).hexdigest(),
        required=[*profile["required"], "double-zero"],
    )
    write("regression-policy", {"version": "2", "profiles": {"linux-cpu": profile}})


if __name__ == "__main__":
    build(Path(sys.argv[1]), sys.argv[2])
