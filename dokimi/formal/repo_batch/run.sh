#!/usr/bin/env bash
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run TLC on the given cfgs (default every cfg/*.cfg) against each "EXPECT:" line; see README.md; POSIX tools only.
# Paths are read from the caller's directory, so they are made absolute before the cd.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
# absolute without touching the file system, so a path that does not exist yet still resolves
abspath() { case $1 in /*) printf '%s\n' "$1" ;; *) printf '%s/%s\n' "$PWD" "$1" ;; esac; }
jar=${TLA2TOOLS_JAR:?set TLA2TOOLS_JAR to tla2tools.jar (v1.8.0)}
jar=$(abspath "$jar")
java=${JAVA:-java}
out=$(abspath "${OUT:-$here/out}")
workers=${TLC_WORKERS:-4}
mkdir -p "$out"
summary="$out/summary.tsv"
printf 'cfg\texpect\tgot\tok\tgenerated\tdistinct\tdepth\tseconds\n' > "$summary"
fail=0
[ $# -gt 0 ] || set -- "$here"/cfg/*.cfg
cfgs=()
for cfg in "$@"; do cfgs+=("$(abspath "$cfg")"); done
for cfg in "${cfgs[@]}"; do
    name=$(basename "$cfg" .cfg)
    expect=$(sed -n 's/^\\\* EXPECT: //p' "$cfg")
    log="$out/$name.log"
    start=$SECONDS
    set +e
    (cd "$here" && "$java" -XX:+UseParallelGC -cp "$jar" tlc2.TLC -workers "$workers" -deadlock -noGenerateSpecTE \
        -metadir "$out/states/$name" -config "$cfg" RepoBatch.tla) > "$log" 2>&1
    rc=$?
    set -e
    secs=$((SECONDS - start))
    if grep -q '^Model checking completed. No error has been found.' "$log"; then
        got=pass
    elif name_v=$(sed -n 's/^Error: Invariant \([^ ]*\) is violated.*/\1/p' "$log" | tail -n 1) && [ -n "$name_v" ]; then
        got="violation $name_v"
    elif name_v=$(sed -n 's/^Error: Temporal property \([^ ]*\) was violated.*/\1/p' "$log" | tail -n 1) \
            && [ -n "$name_v" ]; then
        got="violation $name_v"
    else
        got="error rc=$rc"
    fi
    gen=$(sed -n 's/^[0-9][0-9]* states generated, \([0-9][0-9]*\) distinct.*/\1/p' "$log" | tail -n 1)
    all=$(sed -n 's/^\([0-9][0-9]*\) states generated.*/\1/p' "$log" | tail -n 1)
    depth=$(sed -n 's/.*depth of the complete state graph search is \([0-9][0-9]*\).*/\1/p' "$log" | tail -n 1)
    ok=yes; [ "$got" = "$expect" ] || { ok=NO; fail=1; }
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%.1f\n' "$name" "$expect" "$got" "$ok" "${all:-}" "${gen:-}" "${depth:-}" "$secs" >> "$summary"
done
if command -v column >/dev/null 2>&1; then column -t -s $'\t' "$summary"; else cat "$summary"; fi
exit $fail
