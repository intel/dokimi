------------------------------ MODULE RepoBatch ------------------------------
(***************************************************************************)
(* Copyright (C) 2026 Intel Corporation                                    *)
(* SPDX-License-Identifier: Apache-2.0                                     *)
(*                                                                         *)
(* The cell lifecycle of `repo run` (dokimi/repos/runner.py,               *)
(* dokimi/repos/batch_ledger.py) and what `repo report`                    *)
(* (dokimi/repos/report.py) aggregates from a batch directory.             *)
(*                                                                         *)
(* One batch directory, two tasks (one arm, one model, one rep: a cell per *)
(* task), two worker threads per invocation, up to MaxInv invocations of   *)
(* `repo run` on the batch (a resume, a --force rerun, a crashed one).     *)
(* An "attempt" is one run_cell call that prepared the cell's directory;   *)
(* retries of a cell are later attempts on the same cell name, never a     *)
(* new rep. See README.md for the mapping of every action to the code.     *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS
    Tasks,        \* cell names (task ids; arm/model/rep fixed)
    Workers,      \* ThreadPoolExecutor(jobs) threads
    Configs,      \* execution ids (batch_ledger.execution_id)
    MaxInv,       \* bound on invocations of `repo run` on the batch
    \* Switches for the code as it is, the fix, and the mutations:
    Locked,       \* batch_lock(): flock on _eval/lock held per invocation
    StartCheck,   \* check_batch_executions() before any cell runs
    UniquePlan,   \* the plan never names one cell twice (the fix, #1)
    CancelGuard,  \* Ctrl-C starts no queued cell (pool.map cancels them)
    ReportCheck,  \* the report refuses a row of two execution ids (the fix, #2)
    AcceptStale,  \* MUTATION: a dead attempt's result can still be committed
    CountLog      \* MUTATION: the report counts every commit, not one per cell

ASSUME Locked \in BOOLEAN /\ StartCheck \in BOOLEAN /\ UniquePlan \in BOOLEAN
ASSUME CancelGuard \in BOOLEAN /\ ReportCheck \in BOOLEAN /\ AcceptStale \in BOOLEAN /\ CountLog \in BOOLEAN
ASSUME MaxInv \in Nat \ {0}

None == 0
Range(s) == {s[k] : k \in DOMAIN s}

\* The plans run_batch builds: every cell, a --task subset, and, until the fix,
\* a plan naming one cell twice (`--task a --task a`, `--model m --model m`, or
\* two model strings with one slug, `a/b` and `a:b`).
Plans ==
    LET t1 == CHOOSE t \in Tasks : TRUE
        all == CHOOSE s \in [1..Cardinality(Tasks) -> Tasks] : Range(s) = Tasks
    IN {all, <<t1>>} \cup (IF UniquePlan THEN {} ELSE {<<t1, t1>>})

VARIABLES
    inv,        \* invocations started so far
    live,       \* invocations whose process is alive
    cfg, force, plan, queue, cancelled, errs, exit,  \* per invocation
    wk,         \* per worker: [st, inv, att]
    atts,       \* attempt id -> [inv, cell, cfg]
    owner,      \* cell -> attempt that last prepared (rmtree+mkdir) its dir
    result,     \* cell -> attempt whose record result.json holds (None: no file)
    art,        \* cell -> attempts whose meta/answer/grade files the dir holds
    commits,    \* history: every write of a result.json, <<cell, attempt>>
    ghosts,     \* history: attempts of a crashed process past their artifacts
    stale,      \* history: a result.json was written by a non-owner attempt
    lateStart,  \* history: an attempt started after its invocation was cancelled
    badOk       \* history: an invocation exited 0 with a planned cell unfinished

vars == <<inv, live, cfg, force, plan, queue, cancelled, errs, exit, wk, atts,
          owner, result, art, commits, ghosts, stale, lateStart, badOk>>

Inv == 1..MaxInv
Idle == [st |-> "idle", inv |-> 0, att |-> 0]

Init ==
    /\ inv = 0 /\ live = {}
    /\ cfg = [i \in Inv |-> CHOOSE g \in Configs : TRUE]
    /\ force = [i \in Inv |-> FALSE]
    /\ plan = [i \in Inv |-> <<>>] /\ queue = [i \in Inv |-> <<>>]
    /\ cancelled = [i \in Inv |-> FALSE] /\ errs = [i \in Inv |-> FALSE]
    /\ exit = [i \in Inv |-> "none"]
    /\ wk = [w \in Workers |-> Idle]
    /\ atts = <<>>
    /\ owner = [c \in Tasks |-> None] /\ result = [c \in Tasks |-> None]
    /\ art = [c \in Tasks |-> {}]
    /\ commits = <<>> /\ ghosts = {}
    /\ stale = FALSE /\ lateStart = FALSE /\ badOk = FALSE

CfgOf(a) == atts[a].cfg

\* cmd_repo_run: batch_lock, then run_batch's check_batch_executions over the
\* finished cells (a --force run skips the cells of its own plan).
Start(g, f, p) ==
    /\ inv < MaxInv
    /\ Locked => live = {}
    /\ StartCheck =>
         \A c \in Tasks : result[c] # None =>
            (f /\ c \in Range(p)) \/ CfgOf(result[c]) = g
    /\ LET i == inv + 1 IN
       /\ inv' = i /\ live' = live \cup {i}
       /\ cfg' = [cfg EXCEPT ![i] = g] /\ force' = [force EXCEPT ![i] = f]
       /\ plan' = [plan EXCEPT ![i] = p] /\ queue' = [queue EXCEPT ![i] = p]
    /\ UNCHANGED <<cancelled, errs, exit, wk, atts, owner, result, art,
                   commits, ghosts, stale, lateStart, badOk>>

\* pool.map hands the next planned cell to an idle thread; one() calls run_cell.
\* reused_result: a finished cell is kept unless --force (another execution: error).
\* Otherwise run_cell rmtree's and recreates the dir, which drops result.json.
Take(w, i) ==
    /\ i \in live /\ wk[w].st = "idle" /\ queue[i] # <<>>
    /\ CancelGuard => ~cancelled[i]
    /\ LET c == Head(queue[i]) IN
       /\ queue' = [queue EXCEPT ![i] = Tail(queue[i])]
       /\ IF result[c] # None /\ ~force[i]
            THEN /\ errs' = [errs EXCEPT ![i] = errs[i] \/ CfgOf(result[c]) # cfg[i]]
                 /\ UNCHANGED <<wk, atts, owner, result, art, lateStart>>
            ELSE LET a == Len(atts) + 1 IN
                 /\ atts' = Append(atts, [inv |-> i, cell |-> c, cfg |-> cfg[i]])
                 /\ owner' = [owner EXCEPT ![c] = a]
                 /\ result' = [result EXCEPT ![c] = None]
                 /\ art' = [art EXCEPT ![c] = {}]
                 /\ wk' = [wk EXCEPT ![w] = [st |-> "run", inv |-> i, att |-> a]]
                 /\ lateStart' = (lateStart \/ cancelled[i])
                 /\ UNCHANGED errs
    /\ UNCHANGED <<inv, live, cfg, force, plan, cancelled, exit, commits, ghosts,
                   stale, badOk>>

\* The agent runs (it may time out: that is an outcome, the cell is graded),
\* then meta.json, events.jsonl, answer.txt, task.json and the grade are written
\* by path into the cell's directory, whatever attempt owns it now.
Work(w) ==
    /\ wk[w].st = "run"
    /\ LET c == atts[wk[w].att].cell IN
       art' = [art EXCEPT ![c] = art[c] \cup {wk[w].att}]
    /\ wk' = [wk EXCEPT ![w].st = "art"]
    /\ UNCHANGED <<inv, live, cfg, force, plan, queue, cancelled, errs, exit,
                   atts, owner, result, commits, ghosts, stale, lateStart, badOk>>

\* _write_json(rd / "result.json"): mkstemp + os.replace into the path.
WriteResult(c, a) ==
    /\ result' = [result EXCEPT ![c] = a]
    /\ commits' = Append(commits, <<c, a>>)
    /\ stale' = (stale \/ owner[c] # a)

Commit(w) ==
    /\ wk[w].st = "art"
    /\ WriteResult(atts[wk[w].att].cell, wk[w].att)
    /\ wk' = [wk EXCEPT ![w] = Idle]
    /\ UNCHANGED <<inv, live, cfg, force, plan, queue, cancelled, errs, exit,
                   atts, owner, art, ghosts, lateStart, badOk>>

\* The process dies (SIGKILL, OOM, host reboot): its threads die with it, the
\* flock is released, a cell past its artifacts has no result.json (missing).
Crash(i) ==
    /\ i \in live
    /\ live' = live \ {i}
    /\ exit' = [exit EXCEPT ![i] = "fail"]
    /\ ghosts' = ghosts \cup {wk[w].att : w \in {v \in Workers : wk[v].inv = i /\ wk[v].st = "art"}}
    /\ wk' = [w \in Workers |-> IF wk[w].inv = i THEN Idle ELSE wk[w]]
    /\ UNCHANGED <<inv, cfg, force, plan, queue, cancelled, errs, atts, owner,
                   result, art, commits, stale, lateStart, badOk>>

\* MUTATION AcceptStale: the late result of a dead attempt is still written.
LateCommit(a) ==
    /\ AcceptStale /\ a \in ghosts
    /\ WriteResult(atts[a].cell, a)
    /\ ghosts' = ghosts \ {a}
    /\ UNCHANGED <<inv, live, cfg, force, plan, queue, cancelled, errs, exit, wk,
                   atts, owner, art, lateStart, badOk>>

\* Ctrl-C: KeyboardInterrupt in the main thread, inside pool.map.
Cancel(i) ==
    /\ i \in live /\ ~cancelled[i]
    /\ cancelled' = [cancelled EXCEPT ![i] = TRUE]
    /\ UNCHANGED <<inv, live, cfg, force, plan, queue, errs, exit, wk, atts,
                   owner, result, art, commits, ghosts, stale, lateStart, badOk>>

\* The pool joins its threads; cmd_repo_run exits 0 only without errors and
\* without an interrupt. With CancelGuard the queued cells are dropped.
Finish(i) ==
    /\ i \in live
    /\ queue[i] = <<>> \/ (CancelGuard /\ cancelled[i])
    /\ \A w \in Workers : wk[w].inv # i
    /\ live' = live \ {i}
    /\ LET ok == ~cancelled[i] /\ ~errs[i] IN
       /\ exit' = [exit EXCEPT ![i] = IF ok THEN "ok" ELSE "fail"]
       /\ badOk' = (badOk \/ (ok /\ \E c \in Range(plan[i]) : result[c] = None))
    /\ UNCHANGED <<inv, cfg, force, plan, queue, cancelled, errs, wk, atts, owner,
                   result, art, commits, ghosts, stale, lateStart>>

Next ==
    \/ \E g \in Configs, f \in BOOLEAN, p \in Plans : Start(g, f, p)
    \/ \E w \in Workers, i \in Inv : Take(w, i)
    \/ \E w \in Workers : Work(w) \/ Commit(w)
    \/ \E i \in Inv : Crash(i) \/ Cancel(i) \/ Finish(i)
    \/ \E a \in ghosts : LateCommit(a)

\* Progress: a live thread keeps working and the pool joins. Nothing forces a
\* new invocation, a crash or a Ctrl-C.
Fairness ==
    /\ \A w \in Workers : WF_vars(Work(w)) /\ WF_vars(Commit(w))
    /\ \A w \in Workers, i \in Inv : WF_vars(Take(w, i))
    /\ \A i \in Inv : WF_vars(Finish(i))

Spec == Init /\ [][Next]_vars
FairSpec == Spec /\ Fairness

-----------------------------------------------------------------------------
(* What `repo report` reads: one result.json per cell directory.           *)
Done == {c \in Tasks : result[c] # None}
Samples(c) ==
    IF CountLog THEN Cardinality({k \in DOMAIN commits : commits[k][1] = c})
    ELSE IF c \in Done THEN 1 ELSE 0
Planned == UNION {Range(plan[i]) : i \in 1..inv}
Missing == Planned \ Done
Mixed == \E c, d \in Done : CfgOf(result[c]) # CfgOf(result[d])
\* The cells a report counts: with ReportCheck a mixed row is refused (exit 1).
Counted == IF ReportCheck /\ Mixed THEN {} ELSE Done

TypeOK ==
    /\ inv \in 0..MaxInv /\ live \subseteq Inv
    /\ \A w \in Workers : wk[w].st \in {"idle", "run", "art"}

\* P1 A retry of a cell is not another sample: a cell is one sample at most.
RetryNotRepetition == \A c \in Tasks : Samples(c) <= 1
\* P2 A counted result is a finished attempt of that very cell, and the files
\*    beside it (meta, answer, grade) are that attempt's and no other's.
OnlyValidAttempts ==
    \A c \in Done : atts[result[c]].cell = c /\ art[c] = {result[c]}
\* P3 No result.json is written by an attempt that no longer owns the dir.
NoStaleOverwrite == ~stale
\* P4 The results a report counts in one row ran under one execution id.
OneConfig == \A c, d \in Counted : CfgOf(result[c]) = CfgOf(result[d])
\* P5 `repo run` exits 0 only when every planned cell has a result; the
\*    report's missing list is exactly the planned cells without one.
PartialNotComplete == ~badOk
\* P6 Ctrl-C: no attempt starts after it, and the run does not exit 0.
CancelContract ==
    /\ ~lateStart
    /\ \A i \in Inv : cancelled[i] => exit[i] # "ok"
\* P7 (liveness, under Fairness) every invocation ends.
InvocationEnds == \A i \in Inv : (i \in live) ~> (i \notin live)

\* Vacuity witnesses: each must be VIOLATED, showing the scenario is reachable.
NoRetryHappens == \A c \in Tasks : Cardinality({k \in DOMAIN commits : commits[k][1] = c}) <= 1
NoCrashAfterArtifacts == ghosts = {}
NoCancel == \A i \in Inv : ~cancelled[i]
NoResumeAfterCrash == ~(\E i, j \in 1..inv : i < j /\ exit[i] = "fail" /\ exit[j] = "ok")
=============================================================================
