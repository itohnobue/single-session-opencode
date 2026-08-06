# Project-Specific — single-session-opencode

## Temporary Files

You can use the `tmp/` subfolder in the current project folder to save any temporary files if needed.
This is useful for storing intermediate results, reports, or data during multi-step work.

**Path resolution:** All `tmp/` paths resolve to `$REPO_ROOT/tmp/` where `$REPO_ROOT` is the absolute path to the repository root (the directory where `opencode` was launched). Always reference `tmp/` paths relative to `$REPO_ROOT`.

---

## Agents

109 specialized AI agents for OpenCode. Agents are stored in `.opencode/agents/` as Markdown files with YAML frontmatter.

**Discovery:** Do FULL read of `.opencode/agents/INDEX.md` for the full categorized agent directory (109 agents grouped by domain). Pick the MOST specialized agent — domain-specific checklists and anti-patterns only work when the agent matches the domain.

### Agent Categories

| Category | Count | Examples |
|----------|-------|----------|
| Language Implementation | 22 | python-pro, golang-pro, rust-pro, typescript-pro |
| Web Frameworks | 10 | react-pro, nextjs-pro, django-pro, fastapi-pro |
| Architecture & Design | 9 | backend-architect, api-designer, microservices-architect |
| DevOps & Infrastructure | 11 | devops-engineer, kubernetes-architect, cloud-architect |
| Security | 6 | security-reviewer, penetration-tester, threat-modeling-pro |
| Database | 5 | postgres-pro, sql-pro, database-architect |
| Testing & Quality | 5 | code-reviewer, tdd-guide, test-automator |
| AI & ML | 5 | ai-engineer, ml-engineer, prompt-engineer |
| Frontend & Mobile | 5 | frontend-developer, ios-pro, ui-designer |
| Documentation | 7 | documentation-pro, technical-writer, docs-architect |
| Incident & Troubleshooting | 4 | incident-responder, debugger, devops-troubleshooter |
| Specialized | 20 | build-engineer, cli-developer, product-manager, web-searcher, etc. |

### Agent Selection

Most specialized wins (e.g., postgres-pro over database-optimizer). Split hybrid tasks into subtasks with different agents.

---

## Memory System

**NEVER use MEMORY.md for anything.** MEMORY.md is the built-in auto-memory system and is completely separate from this project's memory system. Do not read, write, or reference MEMORY.md. Use only `knowledge.md` and `session.md` via the `memory.sh` tool (or `memory.bat` on Windows).

Two-tier: **Knowledge** (`knowledge.md`) permanent, **Session** (`session.md`) temporary.

| Question | Use |
|----------|-----|
| Will this help in future sessions? | **Knowledge** |
| Current task only? | **Session** |
| Discovered a gotcha/pattern/config? | **Knowledge** |
| Tracking todos/progress/blockers? | **Session** |

### Knowledge

```bash
./.opencode/tools/memory.sh add <category> "<content>" [--tags a,b,c]   # memory.bat on Windows
```

| Category | Save When |
|----------|-----------|
| `architecture` | System design, service connections, ports |
| `gotcha` | Bugs, pitfalls, non-obvious behavior |
| `pattern` | Code conventions, recurring structures |
| `config` | Environment settings, credentials |
| `entity` | Important classes, functions, APIs |
| `decision` | Why choices were made |
| `discovery` | New findings about codebase |
| `todo` | Long-term tasks to remember |
| `reference` | Useful links, documentation |
| `context` | Background info, project context |

**Tags:** Cross-cutting concerns (e.g., `--tags redis,production,auth`). **Skip:** Trivial, easily grep-able, duplicates.

**After tasks:** State "**Memories saved:** [list]" or "**Memories saved:** None"

**Other:** `search "<query>"`, `list [--category CAT]`, `delete <id>`, `stats`

### Session

Tracks current task. Persists until cleared.

**Categories:** `plan`, `todo`, `progress`, `note`, `context`, `decision`, `blocker`. **Statuses:** `pending` → `in_progress` → `completed` | `blocked`.

```bash
./.opencode/tools/memory.sh session add todo "Task" --status pending
./.opencode/tools/memory.sh session show                    # View current
./.opencode/tools/memory.sh session update <id> --status completed
./.opencode/tools/memory.sh session delete <id>
./.opencode/tools/memory.sh session clear                   # Current only
./.opencode/tools/memory.sh session clear --all             # ALL sessions
```

### Multi-Session

Multiple CLI instances work without conflicts. Resolution: `-S` flag > `MEMORY_SESSION` env > `.opencode/current_session` file > `"default"`.

```bash
./.opencode/tools/memory.sh session use feature-auth        # Switch session
./.opencode/tools/memory.sh -S other session add todo "..." # One-off
./.opencode/tools/memory.sh session sessions                # List all
```

---

## Web Research — MANDATORY AND PROACTIVE

**The model MUST research before answering.** Whenever any external information is needed — facts, specifications, documentation, versions, APIs, news, best practices, unfamiliar technologies — the model MUST use `web_search.sh` BEFORE answering or proceeding. Never answer from training memory alone when the information is verifiable online.

**PROACTIVE USE:** Research is not limited to explicit requests. Whenever the model judges that external knowledge would improve the answer or the work — unfamiliar tech, recent changes, API contracts, breaking changes, alternatives — it researches FIRST, on its own initiative, without waiting to be asked.

**Mechanics — ALL internet research must go through `web_search.sh`** — no exceptions. This means: no built-in websearch tool, no WebFetch tool, no `curl` against APIs, no manual GitHub API calls, no `wget`, nothing else. Every time you need information from the internet, use `./.opencode/tools/web_search.sh "query"` (or `.opencode/tools/web_search.bat` on Windows):
- **One query per call** — run each query as a separate `web_search.sh` invocation. Never combine multiple queries into a single call. Run calls **sequentially** (one after another, not in parallel) to avoid hitting API rate limits
- **Always use default options** — never add `-s`, `--max-results`, or any result-limiting flags. Let the tool use its built-in defaults
- **Scientific queries: add `--sci`** for CS, physics, math, engineering (arXiv + OpenAlex)
- **Medical queries: add `--med`** for medicine, clinical trials, biomedical (PubMed + Europe PMC + OpenAlex)
- **Tech queries: add `--tech`** for software dev, DevOps, IT, startups (Hacker News + Stack Overflow + Dev.to + GitHub)

**Deep research:** For large multi-query research tasks, the model may delegate to the `web-searcher` agent (`.opencode/agents/web-searcher.md`) via the task tool — it is designed for comprehensive search + fetch + report. The model decides when direct `web_search.sh` calls suffice vs. when the agent is warranted.

### Research Confidence Tiers

When presenting research findings, always state their confidence tier. Do NOT present research findings as established facts unless they are CONFIRMED (≥2 independent sources agree).

- **CONFIRMED** — ≥2 independent sources agree on the fact
- **LIKELY** — one solid source, or multiple weaker ones consistent
- **TENTATIVE** — single source, plausibility uncertain
- **SPECULATIVE** — inference beyond the sources; clearly label as such

State the tier explicitly in your answer for each key research claim (e.g., "CONFIRMED: …", "LIKELY: …"). This is especially important when research conflicts with the user's assumptions or when the information will drive code changes.

**Note:** Always use forward slashes (`/`) in paths for agent tool run, even on Windows. Dependencies handled automatically via uv.

---

## Interaction Model — a dialog with the user

This suite is a **dialog**, not an autonomous pipeline. The model solves the task at hand; the user is the partner in the session. The user can — and will — interject, redirect, ask questions, and change course at any moment.

**MANDATORY:**
- **Plan before non-trivial work.** Before starting a multi-step task, tell the user your plan/approach in a few lines — what you'll do, in what order, and any assumptions or open choices. After the user acknowledges (or interjects), proceed.
- **Surface decisions.** Whenever a genuine fork in the road appears (different approaches with real trade-offs, ambiguous requirements, scope questions), present the options briefly with a recommendation — then proceed with your best judgment if the user does not pick.
- **Keep the user in the loop.** Report meaningful progress, findings, and course changes as they happen. A short line is enough; do not silently disappear into a long operation.
- **Respond to interjections immediately.** The user's message always takes priority over the current step. Adjust course on the spot.
- **Don't pause for approval of obvious steps.** Planning, research, and execution that are clearly implied by the task proceed without asking. The dialog is about direction and decisions, not permission for every action.
- **Never ask "should I continue?"** — continue, and report.
- **Work ends only on a genuine blocker** — environment failure, missing files, corrupted state, unresolvable missing dependency. Report the blocker and what remains.

**Scope — main model only.** This dialog model applies to the MAIN model in the session. Subagents are different: they are fully autonomous workers that never talk to the user. A subagent executes its one task, makes its own decisions, and reports back to the main model — which then relays results to the user. The subagent coordination templates (`.opencode/templates/coordination-*.txt`) keep their own autonomy rules; they are intentionally NOT overridden by this section.

---

## Single-Session Workflow

This is a single-session agent suite — NOT an orchestration pipeline. The model does the work itself, in the current session, in dialog with the user. Subagents are a tool the model uses at its own discretion, never a mandated pipeline.

### How it works

1. **The model does the work directly.** The main model is the sole worker. It reads code, writes code, runs commands, verifies results, and delivers — all in the current session.
2. **The model solves most work directly.** Subagents are the exception, not the default: the model spawns one only when a serious subtask genuinely benefits from a specialist's domain expertise or isolated context — and it makes that call itself, on sight. There is no planner, no manifest, no stage structure.
3. **The user works alongside the model.** The user interjects, redirects, asks questions, or assigns new tasks at any point mid-session. The model responds immediately — there is no "stage boundary" to respect.
4. **Tasks are single-session sized.** This suite is for focused, self-contained tasks the model can complete in one session with the user. It is not for orchestrator-level multi-stage productions.

### Plan display rule

Before starting any non-trivial task, output your plan as text to the user — steps, order, approach, assumptions, open choices. Write it in the session, not just to a file. Display first, then proceed. For trivial tasks (a one-liner fix, a quick answer), skip the formal plan — a short statement of intent suffices.

### When to spawn a subagent

**Default: do the work directly.** The model solves simple and medium tasks itself. Subagents are for serious tasks only — spawn one when the task is genuinely hard AND delegation makes it materially better.

**Spawn ONLY when ALL of these hold:**
1. **The task is serious.** A deep security audit, a large refactor, a complex cross-module analysis, an unfamiliar domain, a non-trivial implementation (a real feature, module, or subsystem — not a one-liner fix, not a routine edit, not a question the model can answer directly).
2. **A specialist makes it materially better.** The subagent's domain checklists and anti-patterns produce a result the model would not reach alone (e.g., an idiomatic Rust refactor via `rust-pro`, a query optimization via `postgres-pro`, a security review via `security-reviewer`, a real implementation via the matching language specialist).
3. **Context protection matters.** The subtask would fill a meaningful part of the model's context with reading/analysis that the subagent can absorb in its own isolated session, returning only a compact result (report + findings).

Spawn when ANY of these match:
- A subtask spans a domain the model would do better with a specialist's checklist (e.g., a deep security review, a complex SQL schema, an idiomatic Rust refactor)
- A subtask is large and independent — the subagent holds the full scope in its own context while the model continues the main work
- **Serious implementation work** — building a real feature, module, or subsystem; complex algorithmic code; a substantial component in a language the model should delegate to the matching specialist (e.g., `python-pro`, `typescript-pro`, `golang-pro`, `swift-pro`). The specialist writes it idiomatic and correct; the model reviews and integrates
- A subtask benefits from isolation (audits, reviews, research) so the model's current context doesn't bias it
- The model needs a second opinion or an independent check of its own work

**Do NOT spawn when:**
- The work is simple, well-understood, or would take more coordination than doing it directly
- The model can produce a correct result itself without excessive context use — delegation adds overhead, not quality
- The only benefit would be perceived parallelism or "using the machinery" — there is no quota and no obligation to spawn

**Mandatory exceptions (always used, not optional):**
- **Web research** — ANY external information need goes through `web_search.sh` first (see Web Research section). Research is the standing exception to "solve it yourself": the model does not guess facts it can verify online.
- **Adversarial check** — after high-priority/high-risk work, `adversarial-reviewer` MUST verify the result (see Quality Practices).

### How to spawn

All 109 agents are native opencode subagents, auto-loaded from `.opencode/agents/*.md`:

1. Read the agent's `.md` file — always fresh re-read before delegating
2. Assemble a task prompt with `assemble-task.sh`:
   ```bash
   .opencode/tools/assemble-task.sh -a AGENT -t TYPE -n NAME --task tmp/{NAME}-task.txt
   ```
   Types: `review` (coordination-review + severity + quality-rules-review), `code` (coordination-code + quality-rules-code), `research` (coordination-review + quality-rules-review). Produces `tmp/{NAME}-task-prompt.txt`.
3. Delegate via the `task` tool — pass the file path with a read-and-execute instruction, NOT the full content:
   ```
   task(description="<3-5 words>", prompt="Read this file. Strictly follow instructions there and execute the described task: tmp/{NAME}-task-prompt.txt", subagent_type="<AGENT>")
   ```
4. The task tool runs the agent as a native opencode subagent (isolated child session, full project permissions). It blocks until the subagent completes and returns only its final summary. Report: `tmp/{NAME}-report.md` (the subagent writes it).

**Task file contents:** PROJECT, YOUR TASK (KEY FILES, CONTEXT, SCOPE), MUST ANSWER questions. Write `tmp/{NAME}-task.txt`, then assemble. Code agents get a WRITABLE FILES section listing exactly which source files they may modify.

**Task prompt self-sufficiency (MANDATORY):** All per-task context must live in the task prompt — key files, scope, constraints, questions. Do NOT rely on AGENTS.md or the agent's `.md` as the operating manual for task specifics. The agent gets a self-contained assignment.

**Parallel spawns:** Only when several genuinely independent serious subtasks exist — issue multiple `task` calls in ONE message so they run concurrently. Keep parallel batches reasonable (up to ~5); coordination overhead grows with count.

**Spawn discipline:**
- **One task per agent.** A subagent executes exactly one task and writes one report. No chained multi-task agents.
- **No two agents edit the same file in parallel** (read overlap is fine). If parallel work needs the same file, split by content or sequence the agents.
- **Respawn discipline:** if an agent fails or produces wrong output, diagnose the root cause (bad prompt? wrong agent? environment?), fix it, and re-issue. Maximum 3 respawn attempts per agent (name them `-r2`, `-r3`). After 3 failures, stop and either do the work yourself or discuss the approach with the user.

### Reviewing agent output

- Check the report exists and is non-empty — that's the primary gate
- Read the report's findings and apply them to the main task
- If an agent's output is wrong or incomplete: diagnose (bad prompt? wrong agent?), fix the task, and re-spawn with corrections (see spawn discipline above)
- There is no mandated verification pipeline, no second opinions, no stage structure — the model reviews agent output with its own judgment, as it would its own work. The one exception is the adversarial gate for high-priority work (see Quality Practices below).

---

## Quality Practices

### Verify before claiming (grep first)

Before claiming something is missing, broken, or unimplemented — grep for existing guards, handlers, or implementations first. Search the codebase for the thing you think is absent before reporting it absent. A claim like "there is no validation here" requires a search that confirms it.

### Self-review after non-trivial code

After writing or modifying non-trivial code: re-read your own diff, run the available tests/build/lint, and check edge cases before delivering. Present the result as reviewed, with test results stated. For significant or security-sensitive changes, consider spawning `code-reviewer` for an independent pass.

### Adversarial check for high-priority work

For very important, high-priority, or high-risk work (production-critical changes, security-sensitive code, irreversible operations, large refactors), the model MUST use the `adversarial-reviewer` agent to check the results for errors before delivering. The adversarial reviewer tries to FALSIFY the work: it reads the code with full surrounding context, searches exhaustively for counter-evidence, errors, and missed edge cases, and reports what survives as CONFIRMED issues.

How to use it:
- After the work is complete (code written or changes made), spawn `adversarial-reviewer` with a task that describes what was done and asks it to hunt for errors, regressions, and unhandled edge cases in the result
- Include KEY FILES (the files that were changed), CONTEXT, and MUST ANSWER questions like: "Are there any bugs, edge cases, or regressions in this change? Is the change correct in all call paths?"
- Treat its CONFIRMED findings as real issues — fix them (directly or via another subagent), then re-verify
- This is a quality gate for high-stakes work only — do not use it for routine changes where self-review and tests suffice

### Don't redo work without evidence

Never redo work that was already done correctly unless evidence shows it was wrong. If a previous attempt exists, inspect why it failed or was incomplete before replacing it — don't rebuild from scratch out of habit.

### Reporting severity

When reporting problems or findings to the user, rate their severity so the user can prioritize:

| Level | Criteria |
|-------|----------|
| **None** | No functional impact. Comment, formatting, variable rename. |
| **Low** | Minor, immediately reversible. Dev tooling, internal logging, tests. |
| **Medium** | User-facing, visible but contained. |
| **High** | Core product function, data mutation, wide blast radius. |
| **Critical** | Permanent harm possible — destruction of pre-existing assets, data loss that cannot be recovered, secret exposure, auth bypass. |

Label findings with their severity (e.g., "HIGH: …") when reporting more than one issue or when anything is at MEDIUM+.

### Regression awareness (git-aware notes)

When working on a codebase with git history: before assuming a problem is new, check whether the cited lines were touched by prior fix/audit commits (`git log --all --format="%h %s" | grep -i "production\|check\|fix\|audit"`). If the location was previously fixed and the issue is back, flag it as a repeat-regression — the previous fix was incomplete, and this one needs extra care (verify the root cause, not just the symptom).

---

## Error Handling

| Scenario | Action |
|----------|--------|
| No report after exit | Diagnose failure from the task result / missing report. Fix root cause (bad prompt? missing dependency? environment?). Re-issue the task call. |
| Agent claims success but output wrong | Diagnose why (bad prompt? misunderstood task?). Fix the prompt/task. Re-issue. |
| Agent aborted (same error 3×) | Diagnose root cause, fix environment/config, re-issue the task call. If it fails a 4th time, do the work directly or discuss with the user. |
| 2+ agents fail same env error | STOP respawning. Diagnose environment first. |

---

## Delivery

- Write final results to the user in the session — summaries, reports, files changed, severity-labeled findings
- Clean up temporary task files: `rm -f tmp/*-task-prompt.txt tmp/*-task.txt` (keep reports, logs, memory)
- Save non-trivial discoveries to knowledge (`memory.sh add`) and task state to session (`memory.sh session add`)
