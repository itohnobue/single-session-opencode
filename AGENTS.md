# Project-Specific — single-session-opencode

## Skills (Workflows)

Workflows are available as skills in `.opencode/skills/` directory. Use `/skill-name` to invoke. Skills are utility operations invoked by the model as needed.

---

## Temporary Files

You can use the `tmp/` subfolder in the current project folder to save any temporary files if needed.
This is useful for storing intermediate results, reports, or data during multi-step work.

**Path resolution:** All `tmp/` paths resolve to `$REPO_ROOT/tmp/` where `$REPO_ROOT` is the absolute path to the repository root (the directory where `opencode` was launched). Always reference `tmp/` paths relative to `$REPO_ROOT`.

---

## Agents

114 specialized AI agents for OpenCode. Agents are stored in `.opencode/agents/` as Markdown files with YAML frontmatter.

**Discovery:** Do FULL read of `.opencode/agents/INDEX.md` for the full categorized agent directory (114 agents grouped by domain). Pick the MOST specialized agent — domain-specific checklists and anti-patterns only work when the agent matches the domain.

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
| Specialized | 22 | build-engineer, cli-developer, product-manager, web-searcher, etc. |

### Agent Selection

Most specialized wins (e.g., postgres-pro over database-optimizer). Split hybrid tasks into subtasks with different agents.

---

## Memory System

**NEVER use MEMORY.md for anything.** MEMORY.md is the built-in auto-memory system and is completely separate from this project's memory system. Do not read, write, or reference MEMORY.md. Use only `knowledge.md` and `session.md` via the `memory.sh` tool.

Two-tier: **Knowledge** (`knowledge.md`) permanent, **Session** (`session.md`) temporary.

| Question | Use |
|----------|-----|
| Will this help in future sessions? | **Knowledge** |
| Current task only? | **Session** |
| Discovered a gotcha/pattern/config? | **Knowledge** |
| Tracking todos/progress/blockers? | **Session** |

### Knowledge

```bash
./.opencode/tools/memory.sh add <category> "<content>" [--tags a,b,c]
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

Synthesize results into a report or answer. **Note:** Always use forward slashes (`/`) in paths for agent tool run, even on Windows. Dependencies handled automatically via uv.

---

## Autonomy

The model runs tasks to completion without unnecessary stops. The operator is present in the session and may interject, redirect, or ask questions at any time — the model responds immediately. But the model does not pause its own progress waiting for decisions it can make itself.

**MANDATORY:**
- The operator interjects freely — when they do, respond immediately and adjust course. The operator is an active participant, not a reviewer at the end of a pipeline.
- Do NOT pause work or ask for approval to proceed when the path is clear. Ambiguity, multiple valid options, or an unclear instruction is never a reason to stop: interpret, choose the best option, document, proceed.
- Do not ask "should I continue?" or wait for confirmation between steps — keep working unless the operator redirects.
- Work ends only on a genuine blocker — environment failure, missing files, corrupted state, unresolvable missing dependency. Report the blocker and what remains.
- Any rule elsewhere (AGENTS.md, agent `.md` profiles, templates) that says "ask the user", "ask for clarification", "confirm before", or "ask the domain owner" is overridden by this section.

---

## Single-Session Workflow

This is a single-session agent suite — NOT an orchestration pipeline. The model does the work itself, in the current session, with the operator watching. Subagents are a tool the model uses at its own discretion, never a mandated pipeline.

### How it works

1. **The model does the work directly.** The main model is the sole worker. It reads code, writes code, runs commands, verifies results, and delivers — all in the current session.
2. **The model decides when to spawn a subagent.** There is no planner, no manifest, no verification pipeline, no stage structure. The model evaluates each piece of work itself: if a subtask would benefit from a specialist's dedicated context (large independent module, unfamiliar domain, complex analysis, focused review), the model spawns a subagent. Otherwise it does the work directly.
3. **The operator works alongside the model.** The operator can interject, redirect, ask questions, or assign new tasks at any point mid-session. The model responds immediately — there is no "stage boundary" to respect.
4. **Tasks are single-session sized.** This suite is for focused, self-contained tasks the model can complete in one session with the operator. It is not for orchestrator-level multi-stage productions.

### When to spawn a subagent

The model uses its judgment. Spawn when ANY of these match:

- A subtask spans a domain the model would do better with a specialist's checklist (e.g., a deep security review, a complex SQL schema, an idiomatic Rust refactor)
- A subtask is large and independent — parallel subagents can work concurrently on disjoint areas while the model continues elsewhere
- A subtask benefits from isolation (audits, reviews, research) so the model's current context doesn't bias it
- The model needs a second opinion or an independent check of its own work

Do NOT spawn when the work is simple, well-understood, or would take more coordination than doing it directly. Prefer doing the work directly by default.

### How to spawn

All 114 agents are native opencode subagents, auto-loaded from `.opencode/agents/*.md`:

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

**Parallel spawns:** For independent subtasks, issue multiple `task` calls in ONE message — all run concurrently. Keep parallel batches reasonable (up to ~5); coordination overhead grows with count.

### Reviewing agent output

- Check the report exists and is non-empty — that's the primary gate
- Read the report's findings and apply them to the main task
- If an agent's output is wrong or incomplete: diagnose (bad prompt? wrong agent?), fix the task, and re-spawn with corrections
- There is no mandated verification pipeline, no second opinions, no adversarial stages — the model reviews agent output with its own judgment, as it would its own work

---

## Error Handling

| Scenario | Action |
|----------|--------|
| No report after exit | Diagnose failure from the task result / missing report. Fix root cause (bad prompt? missing dependency? environment?). Re-issue the task call. |
| Agent claims success but output wrong | Diagnose why (bad prompt? misunderstood task?). Fix the prompt/task. Re-issue. |
| Agent aborted (same error 3×) | Diagnose root cause, fix environment/config, re-issue the task call. |
| 2+ agents fail same env error | STOP respawning. Diagnose environment first. |

---

## Delivery

- Write final results to the operator in the session — summaries, reports, files changed
- Clean up temporary task files: `rm -f tmp/*-task-prompt.txt tmp/*-task.txt` (keep reports, logs, memory)
- Save non-trivial discoveries to knowledge (`memory.sh add`) and task state to session (`memory.sh session add`)
