---
name: handoff
description: Structured continuation handoff — retire a subagent run at the workflow's reuse threshold (Mode A) or checkpoint a session at a pause point (Mode B), so a fresh agent or later session continues with zero re-discovery. Use at the reuse threshold or when a handoff/checkpoint is requested.
disable-model-invocation: false
argument-hint: [retire <agent-name> | checkpoint]
---

# Handoff

A handoff is the authoritative carry-over artifact between one run/session and its successor. It is written in a fixed 8-section format so nothing silently drops: intent, state, errors already resolved, and the exact next step survive the replacement.

This skill is the single home for handoff mechanics — the workflow's continuation and retirement rules reference it.

## When to use

- **Mode A — Retire a subagent run.** The lead is at the workflow's reuse threshold (default: 3 reuses per `task_id`, or an earlier retirement decision): one final resume of the retiring run writes the handoff, then a fresh successor is booted.
- **Mode B — Session checkpoint.** The user invokes `/handoff` (or asks for a handoff/checkpoint), or the model judges a pause point (context pressure, ending a session, switching tasks): the main model writes the handoff from its own live state.

## Template (fixed structure — adapt content, never structure)

````markdown
HANDOFF — <task/session name>   (written before replacement)

These notes are the established background from the previous run. Treat them as a
bounded handoff: build on them, confirm their claims against the workspace before
acting, and do not restate or re-derive them.

## Objective and Intent
- <original and evolving goals; quote exact wording when it matters>

## Key Technical Concepts
- <technologies, patterns, conventions in play>

## Files and Artifacts
- <path>: <why it matters / what changed>

## Errors and Fixes
- <error>: <resolution; reviewer/user feedback included>

## Pending Work
- <explicitly requested work not yet completed>

## Current Work
- <precisely what was in progress at this point>

## Next Step
- <the single next action>

## Critical Context
- <decisions and rationale, constraints, open questions>

Rules: terse bullets; exact paths, commands, identifiers, and numbers preserved;
"(none)" for empty sections — never drop a section; completeness over brevity
(no size cap); no meta commentary.
````

## Mode A — Retire a run

**Path rule (MANDATORY):** every Mode A path uses the RETIRING run's literal name — `tmp/<retiring-name>-handoff.md`. Never write `{NAME}` into the successor's task file: assemble-task.sh substitutes `{NAME}` with the successor's name, silently pointing the successor at a non-existent handoff (verified 2026-09-10). Resumes are not assembled, so the resume instruction must carry the literal path as well.

1. Decide retirement (the reuse threshold or an earlier lead call).
2. Resume the retiring run once with a self-contained instruction: "Write your handoff to `tmp/<retiring-name>-handoff.md` using exactly the template below [template included in the instruction], then stop — do not do further work. The handoff file is the deliverable for this final step; it is authorized here and overrides the original WRITABLE FILES directive — leave the report as-is." This final resume is the retirement step itself; the retired run's reuse budget ends with it. If the `task_id` is expired or the run cannot write it, the lead writes the handoff from the report.
3. Completeness check: verify all 8 section headings exist (`grep -c '^## ' tmp/<retiring-name>-handoff.md` — expect 8). "(none)" is acceptable; a missing section is not — resume the retiring run once more to fix it.
4. Do not spawn the successor until the handoff passes the check — no downstream read before the artifact is complete. The successor is a new name, a new run; its task file carries the same task, with the handoff first in PRIOR CONTEXT — written with the retiring run's literal name, `tmp/<retiring-name>-handoff.md` — and the full report as backup. Add the consumption line: "Read the handoff first and treat it as bounded — build on it, confirm its claims against the workspace before acting, and do not restate it."
5. Record the successor's `task_id` (`tmp/<successor-name>-task-id.txt`, the workflow's task-id convention); annotate the retired run's `tmp/<retiring-name>-task-id.txt` with `retired -> <successor-name>`.

## Mode B — Checkpoint the session

1. The main model writes the handoff itself (it holds the live state) to `tmp/handoff-<slug>.md` (or a user-specified path).
2. Add a session note: `./.opencode/tools/memory.sh session add note "handoff: <path>"` (`memory.bat` on Windows).
3. Tell the user the path. Resuming later = "continue from <path>": read the handoff, `session show`, confirm against the workspace, continue from Next Step.

**One active handoff per task:** updates replace it. When the task completes, delete the handoff and its session note — a stale handoff must never trigger a false resume. Workflow-driven sessions detect the active handoff (the `handoff:` session note or the newest `tmp/handoff-*.md`), read it, and continue from Next Step.

## Consuming a handoff

- Read the handoff first; treat it as bounded — build on it, confirm its claims against the workspace before acting, do not restate it.
- The full report and artifacts remain the depth source; the handoff is the map to them.
- When a handoff claim contradicts the workspace, the workspace wins — update the handoff rather than following a stale claim.

## Rules

- One handoff per replacement (Mode A) or per task (Mode B), written by the party with live context (retiring run in Mode A, main model in Mode B).
- When the workflow tracks agent `task_id`s, list completed and in-flight ids in `## Critical Context` — a replacement lead resumes incomplete runs instead of redoing them.
- Paths are literal: Mode A `tmp/<retiring-name>-handoff.md`, Mode B `tmp/handoff-<slug>.md` — never `{NAME}` in a successor's task file (see the Mode A path rule).
- "Handoff" is the term everywhere — no "continuation summary" / "handoff summary" variants.
- Handoff files are never matched by the tmp cleanup globs — they survive the routine sweep and are removed only deliberately (Mode B, on task completion).
