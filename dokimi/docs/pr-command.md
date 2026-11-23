<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Run dokimi from a pull request comment

A maintainer comments `/dokimi run` on a pull request; the configured dokimi checks run on
the pull request's head commit, and one comment on the pull request shows the
`dokimi report opportunities` table: the findings ranked into a queue
of fixes. The next `/dokimi run` updates the same comment.

Copy [examples/dokimi-pr-command.yml](examples/dokimi-pr-command.yml) to
`.github/workflows/` in your repository, replace `<sha>` with the dokimi commit you pin,
and replace the `Run the checks` step with yours. Each check writes its findings file to
the top level of `$OUT/opportunities`: a novelty artifact, a `repo_static.v1` document or
an `llms.txt` file (`*.json` and `*.txt` are read; anything else is left out).

## Who can run it

Only someone with `write` or `admin` permission on the repository (`maintain` counts as
`write`). `actions/pr-command` reads the permission from
`GET /repos/{owner}/{repo}/collaborators/{login}/permission`; a failed lookup counts as no
permission. Anyone else gets one reply saying the command needs write permission, and no
check runs. The command must be the comment's whole first line (spaces around it are
ignored), optionally followed by one commit: a comment quoting it later, or `/dokimi runs`, does nothing. A comment on an
issue, not a pull request, does nothing. A bot account has no collaborator permission, so it is refused like a reader. A picked-up command gets a 🚀 reaction.

## How the jobs are split

Keep secrets and model keys out of `checks`. Pull request code can access the runner's Actions cache credentials even with `contents: read`. Review the head before invoking the workflow and pin it with `/dokimi run <sha>`.

The trigger is `issue_comment`, which runs in the base repository's context, so the
example keeps the write token away from pull request code:

| Job | Permissions | Runs pull request code |
|---|---|---|
| `gate` (`actions/pr-command`) | `pull-requests: write` (reply, reaction) | no |
| `checks` | `contents: read` | yes, the head commit read by `gate`, checked out with `persist-credentials: false` |
| `comment` (`actions/opportunities-comment`) | `pull-requests: write` | no: it reads the uploaded findings as data |

The checks run on the head commit `gate` reads, which can be a commit pushed after the
comment while the run was queued. To pin what runs, comment `/dokimi run <sha>` (the full
40-hex head commit; a short prefix could be matched by a crafted commit): the gate refuses it with a reply when the head has moved.
Set `require-sha: 'true'` on `actions/pr-command` to accept only that form, for a
repository where a push between the comment and the run is a concern. The
comment text is built from the findings, which quote the pull request's files: `@`
mentions and `<!--` are broken with a zero-width space, so the table cannot ping anyone
or forge the comment's marker. No step interpolates `${{ }}` into a `run:` script; every
value reaches the shell through `env:`.

## Inputs

`actions/pr-command`: `command` (`/dokimi run`), `require-sha` (`false`), `github-token` (the job token). Outputs
`run` (`true`/`false`), `pr`, `head-sha`.

`actions/opportunities-comment`: `inputs-dir` (required), `pr` (required), `head-sha`
(named in the comment), `top-n` (10), `github-token` (the job token). It needs
`actions/setup` first. With no findings file the comment says there is nothing to rank;
when `dokimi report opportunities` fails the comment links the run and the step fails.
