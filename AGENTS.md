# Single-Session OpenCode

## Operating notes

On Windows:
* Expand %USERPROFILE% to full path before running actual commands with it
* Do not use cmd /c it never works

---


## Temporary Files

You can use the `tmp/` subfolder in the current project folder to save any temporary files if needed.
This is useful for storing intermediate results, reports, or data during multi-step work.

**Path resolution:** All `tmp/` paths resolve to `$REPO_ROOT/tmp/` where `$REPO_ROOT` is the absolute path to the repository root (the directory where `opencode` was launched). Always reference `tmp/` paths relative to `$REPO_ROOT`.

---
## Agents

4 agents for OpenCode, built around the **tiered executor pipeline** (context rule → T1 plain / T2 researched / T3 primary per context rule + research-backed s2 → optional verification). Agents are stored in `.opencode/agents/` as Markdown files with YAML frontmatter. Full directory: `.opencode/agents/INDEX.md` — read it before delegating.

| Agent | Role |
|-------|------|
| `prepare-agent` | Research generation for T2/T3 tasks only. Identifies every technology a task touches, researches up to 3 queries per technology (best practices, real domain knowledge, specialist advice), curates the highest-quality material into ONE ≤15KB research-data file (soft max — no minimum). `FOCUS:` parameter defines the specialist identity. Speed-limited by design. |
| `executor` | The single executor for all tiers and work types — executes the assembled task (T1: task context is the briefing; T2/T3: template → RESEARCH DATA → task). No research of its own. |
| `adversarial-reviewer` | Falsification gate — part of the optional VERIFY block (critical issues, acted-on findings, or on demand). Process-only, independent of research data. |
| `web-searcher` | Deep-research fallback for the main model when a task needs beyond the prepare budget. |

Specialist identity is defined by the research-data themes (FOCUS), not by static `.md` personas. Rules below are empirically grounded.

---

## Agent Delegation (tiered executor pipeline)

**The main session is the primary worker** — it solves most work directly, in dialog with the user. Delegation is the exception, chosen by judgment: delegate when the task fits the criteria below. Trivial work (quick answers, small edits, questions, routine changes) never touches agents.

**Use existing agents directly only on a 100% fit.** A task is solved with a single direct agent call ONLY when an existing agent fully matches the job — a pure research question → `web-searcher`; verifying a claim or finding → `adversarial-reviewer`. This is a shortcut for genuinely matching jobs, not a license to route everything through agents: most tasks stay in the session, and if the fit isn't 100%, the path below applies.

**When to delegate:**
1. **No 100%-fit existing agent** exists for the job (the job needs a custom specialist — per-tier: plain executor for T1, research generation plus a dedicated executor for T2/T3).
2. The task is **big and heavy** — a deep audit/review, a large refactor, a complex cross-module analysis, an unfamiliar domain, a non-trivial implementation (a real feature/module/subsystem — not a one-liner, not a routine edit).
3. The task **needs lots of context to execute** — more than ~20% of a 1M-token context window (reading large codebases, very long files, many files): the subagent absorbs that in its own isolated context, returning a compact result.
4. The task **touches current external facts** (versions, APIs, ecosystem behavior, format specs, security advisories) where fresh research materially improves the *report quality* — research is a QUALITY input (precision, breadth), NOT a solver. (Scale qualifier: a small task that just needs a quick lookup is solved in-session with `web_search.sh` — no delegation.)

**The context rule (the ONE general principle all tiers build upon):**
Plain (no research) is used ONLY when the task file already carries rich context — the facts the executor needs (contracts, specs, environment, expected behaviors) are stated in PRIOR CONTEXT or were already researched into it. When the task file is thin and the task depends on facts it does not carry (current external facts: versions, APIs, ecosystem behavior, format specs, security advisories), research is injected to supply what the context lacks. Plain is not better than researched per se — research adds ≈0 on top of supplied context (it only dilutes attention); research is the stable choice when context is thin (best precision and breadth). This rule applies to EVERY tier below, including the primary of a second-opinion run.

**Executor tiers (applications of the context rule):**
- **T1 — plain executor (context rule: rich context only):** assemble WITHOUT `--research-file`; the task file's own context is the briefing. Valid when: (a) self-contained tasks — logic-internal, contracts and expected behaviors stated; (b) well-researched scopes whose facts are already in the task file (main model's own research, prior sessions, project knowledge). Implementations follow the rule too: T1 when specs/contracts are stated, T2 when they depend on current external facts the file does not carry.
- **T2 — researched executor (context rule: thin context):** prepare → assemble WITH `--research-file`. The prepare phase supplies what the task context lacks. Secondary qualifier: when report precision/breadth matters (findings feed triage/fix pipelines, user-facing reviews).
- **T3 — second-opinion runs (the context rule applies to the primary):** a research-backed s2 (one prepare with complementary FOCUS + one s2 executor) on top of a primary whose tier follows the context rule: plain when the task file is rich, researched when it is thin. The best measured combination; role-only s2 is dropped.

**Research tasks** (research IS the deliverable): default to `web-searcher` (100% fit) — a quick/simple lookup goes in-session with `web_search.sh`, substantial multi-query research goes to `web-searcher`. They are delegated ONLY under the same rules as any task: when the research task itself is big/heavy or context-hungry beyond what a single web-searcher run can hold — then it goes through prepare + execute like everything else (prepare searches, the executor synthesizes).

**The flow (per delegated task):**

1. **CHOOSE THE TIER** — apply the context rule: T1 (plain, no prepare — rich context only), T2 (prepare + research — thin context needing external facts, or precision/breadth matters), or T3 (second-opinion runs: primary per the context rule + research-backed s2). For T1 skip to ASSEMBLE without `--research-file`.
2. **PREPARE** (T2/T3 only) — spawn `prepare-agent` with a prepare task (includes `FOCUS: <angles>`, default `correctness, completeness`). Output: `tmp/prepare/<slug>-research.md` (soft max ~15KB, 1-2KB over fine). The prepare agent self-reviews its file before delivery (size, per-tech coverage, confidence tiers, policy baking, source mapping, no raw dumps) — the main model does NOT check; it only acts if the prepare report flags remaining issues. Prepare does research data only: no pre-solving, no trimming, full search output, knowledge fallback if web search fails.
3. **ASSEMBLE** — one command wraps the template, (optionally) injects the research data, and appends the task:
   ```bash
   .opencode/tools/assemble-task.sh -a executor -t TYPE -n {NAME} --task tmp/{NAME}-task.txt [--research-file tmp/prepare/{NAME}-research.md] -o tmp/{NAME}-task-prompt.txt
   ```
   Result structure: template → RESEARCH DATA (T2/T3 only) → task. The standalone `.opencode/tools/inject-research.sh` exists for custom cases (pre-injected task files); do NOT combine both paths — passing `--research-file` with a task file that already contains a `## RESEARCH DATA` section is rejected.
4. **EXECUTE** — spawn `executor` (default for all work types — implementation, deep analysis, investigation). It reads the file, does the work, reports. **The delegation run ends here.** Verification is NOT part of it — it is a separate optional block (see VERIFY below), used for critical issues, acted-on findings, or on demand.

### VERIFY (optional block — critical issues, acted-on findings, or on demand)

Verification is NOT automatic. Run it when: (a) the work is critical/high-risk (production-critical changes, security-sensitive code, irreversible operations); (b) a findings-type task's results will be acted on (triage/fix pipelines, user-facing reviews) — T3 merged outputs carry the most false positives (the s2's speculative tail) and benefit most, as does any output whose task file proved thinner than expected; (c) the user asks, or judgment says the work needs falsification. The block — one agent per stage, with ONE `adversarial-reviewer` per VERIFY block; with many issues, each issue gets its own VERIFY block with its own agents (respect the parallel-spawn rules; never batch multiple issues into one agent run):

1. **REVIEW** — one `executor` (type `review`) per issue (a finding IS an issue): each finding gets its own review run that validates it — root cause, evidence, severity labels (for findings-type deliverables). For implementation deliverables, the review reviews the changes and files severity-labeled findings — each filed finding then becomes an issue of its own with its own block. **If the review produced no MEDIUM+ findings, the VERIFY block ends here** — nothing left to falsify or fix.
2. **ADVERSARIAL** — ONE `adversarial-reviewer` per VERIFY block: a single adversarial run falsifying that issue's MEDIUM+ review claims — false positives get REJECTED, overstated ones WEAKENED with the correct severity; and **challenges the reviewer's "investigated-and-rejected" list** for that issue — reviewers have dismissed real bugs, so the adversarial re-examines those dismissals, not just the filed findings. On T3 merged outputs, prioritize the UNIQUE findings (primary-only and s2-only, per the s2's uniqueness statement) — the both-found core is already double-verified by two independent opinions. Runs STANDALONE — no second-opinion pair.
3. **FIX stage** — every CONFIRMED finding is a real issue: one fresh executor run (`executor`) per finding fixes it, with the findings as additional context. REJECTED/WEAKENED findings are not issues to fix.
4. **Re-verify** — re-run the review + adversarial check on the changed parts (one agent per stage, ONE `adversarial-reviewer` per block — same as the main block; if the re-review produces no MEDIUM+ findings, the block ends here). Any new CONFIRMED findings go back to the FIX stage. Cap the loop at 3 fix passes per issue — if issues persist beyond that, report them to the user as unresolved rather than looping forever.

### Second-opinion rules

- **When:** tasks whose deliverable is FINDINGS/ANALYSIS — research, review, discovery, audits — where the problem must be checked from different angles: at MEDIUM+ severity, when the user asks, or when the first opinion was inconclusive (no CONFIRMED findings but suspicion remains). NOT for implementation tasks — implementations get the optional VERIFY block instead (no second implementation run).
- **How (context rule + research-backed s2 — no role-only s2):** a second-opinion run is T3: the primary runs per the context rule (plain when the task file is rich, researched when thin) AND one prepare with a complementary FOCUS (e.g., primary `security, correctness` → second `performance, maintainability`) feeds the s2 executor. Never the same FOCUS twice — unique catches cluster in the complementary FOCUS areas. The prepare runs CONCURRENTLY with the primary executor; the s2 executor runs after assembly. The s2 recovers unique bugs the primary missed.
- **Merge, never replace:** both the primary and the s2 miss bugs the other holds (FOCUS-induced misses are symmetric). Always merge primary + s2 findings; s2 reports must state which findings are unique to their standpoint vs also found by the primary.
- **Verify the merge (critical tasks):** for critical findings tasks, run the VERIFY block on the MERGED primary+s2 finding set — the adversarial prioritizes the unique findings (primary-only and s2-only); the both-found core needs no re-check (already double-verified).
- **Paths:** every second-opinion run writes to its own paths (`*-s2-*`, `*-s3-*`...). Shared deliverable paths are forbidden — parallel runs collide (observed in testing).

### Executor selection

- `executor` — the single executor for ALL work types (implementation, execution, deep analysis, investigation).
- Research-treatment tiers (T1/T2/T3) are chosen at ASSEMBLE time — see "Executor tiers" above. T1 runs use the same executor without `--research-file`.

### Adversarial — when

- Part of the optional VERIFY block — run it for critical/high-risk delegated work, acted-on findings (triage/fix pipelines, user-facing reviews), when the user asks, or when judgment says the work needs falsification. NOT automatic after every delegation.
- Trivial session work does NOT need it — self-review and tests suffice.
- The model may also run a quick adversarial pass on its own work anytime (outside the block) when judgment says the work is high-risk.

### Prepare task template (main model writes it)

```
PROJECT: Prepare phase for <task> on <repo>
TARGET REPO: <abs path>
REPO_ROOT: <abs path>
WEB SEARCH TOOL: <abs path to web_search.sh>
FOCUS: <angles, e.g. security, correctness>
YOUR TASK (prepare-agent protocol): enumerate every technology, ≤3 queries per tech
  leading with FOCUS angles, select highest-quality material, write ONE ≤15KB research-data file (soft max),
  read target repo AGENTS.md and bake its policies in.
DELIVERABLES: research-data file + prepare report paths
MUST ANSWER: coverage mapping, file size, exclusions, confidence breakdown
```

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

## Web Research — Use When Needed

**Research when the answer depends on it — search-first for external facts.** Whenever the answer or the work depends on external facts — versions, APIs, documentation, news, best practices, unfamiliar technologies — a quick `web_search.sh` is the default, even when you mostly know the answer: the tool is cheap, and confidence is not a reason to skip a search for a fact that will be stated or acted on. Guessing verifiable facts is the failure mode. Memory-only answers for external facts are the exception, allowed when the fact is already in the task file or prior research, or trivially stable (language syntax, math, your own code).

**SCOPE:** research applies to **external facts** — versions, APIs, formats, ecosystem behavior, security advisories. It does NOT make research a quality lever for self-contained code work: code-review detection is not improved by research; research's value is report discipline and breadth. For self-contained work whose facts are already in the task file, solve directly or delegate plain (T1) without preparing research.

**PROACTIVE USE:** Research is not limited to explicit requests. Whenever the model judges that external knowledge would materially improve the answer or the work — unfamiliar tech, recent changes, API contracts, breaking changes, alternatives — it researches on its own initiative, without waiting to be asked.

**Mechanics — when you do search, use `web_search.sh`**: no built-in websearch tool, no WebFetch tool, no `curl` against APIs, no manual GitHub API calls, no `wget` for search. Fetching a specific known URL goes through `web_search.sh --url <url>` (direct fetch mode: one URL per run, full page saved to `tmp/webresearch/<run-id>.txt`, path printed to stdout) — the sanctioned way to get a named page when a search would be wasteful. Use `./.opencode/tools/web_search.sh "query"` (or `.opencode/tools/web_search.bat` on Windows):
- **One query per call** — run each query as a separate `web_search.sh` invocation. Never combine multiple queries into a single call. Run calls **sequentially** (one after another, not in parallel) to avoid hitting API rate limits
- **Fixed tuned defaults** — the tool has no count or format flags: search always fetches 30 results, fetches up to 20 pages, and outputs plain text only. The only flags are the source flags `--sci`/`--med`/`--tech` and `--url` direct fetch — never add count/result-limiting or output-format flags (they do not exist). Let the tool use its built-in defaults
- **DIGEST + FULL REPORT FILE** — search mode prints a compact digest (stats line, FULL REPORT path, per-page previews) and writes the full filtered text to `tmp/webresearch/<run-id>.txt`. The report file IS the product — read or grep the file at the given path for the content you need (grep by URL or term). Never trim the digest with `tail`/`head`/`grep -m` or any other trimming — it is small and carries the FULL REPORT path: trimmed, you lose the link to the reference database. For a specific page's fresh content, fetch it directly with `--url`. The stats line also carries dropped-page counters (farm/stub/rerank/stale/dedup-dropped) when quality filters removed pages.
- **Direct URL fetch: `--url`** — when you need a specific known page (URL from a search result, docs page, paper), use `web_search.sh --url <url>` instead of WebFetch/curl/wget (no query needed — the query is optional in this mode). ONE URL per call: the full page (no char cap) is quality-filtered and saved to its own report file in `tmp/webresearch/`; stdout prints ONLY `Full web page saved at: <path>`. JS-heavy pages (SPAs) are rendered with a headless Chromium shell (chromium-headless-shell — official Google build on macOS/Windows, bundled-libs build on Linux; uv-managed, fetched once into a user cache, headless/background only, no system installs): auto-fallback when static fetch fails, `--render` to force, `--no-render` to disable. Search mode is static-only (no browser).
- **Scientific queries: add `--sci`** for CS, physics, math, engineering (arXiv + OpenAlex)
- **Medical queries: add `--med`** for medicine, clinical trials, biomedical (PubMed + Europe PMC + OpenAlex)
- **Tech queries: add `--tech`** for software dev, DevOps, IT, startups (Hacker News + Stack Overflow + Dev.to + GitHub)
- **Empty results & timeouts are not tool failures** — a non-zero exit with a "No results: …" message on stderr means the query legitimately produced nothing usable (quality filters dropped every page, or all fetches failed) — retry with a different query angle. Each run is self-bounded by a 300s wall-clock timeout (env-overridable via `WEB_RESEARCH_TIMEOUT_SECONDS`); on timeout it exits non-zero with a "wall-clock timeout" message.

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
2. **The model solves most work directly.** Subagents are the exception, not the default: the model spawns one only when the subtask is big and heavy or needs lots of context to execute (see Agent Delegation) — and it makes that call itself, on sight. There is no planner, no manifest, no stage structure.
3. **The user works alongside the model.** The user interjects, redirects, asks questions, or assigns new tasks at any point mid-session. The model responds immediately — there is no "stage boundary" to respect.
4. **Tasks are single-session sized.** This suite is for focused, self-contained tasks the model can complete in one session with the user. It is not for orchestrator-level multi-stage productions.

### Plan display rule

Before starting any non-trivial task, output your plan as text to the user — steps, order, approach, assumptions, open choices. Write it in the session, not just to a file. Display first, then proceed. For trivial tasks (a one-liner fix, a quick answer), skip the formal plan — a short statement of intent suffices.

### When to spawn a subagent

**The main session is the default worker — delegate when the task fits the Agent Delegation criteria:**


**Decision order:** (1) session work → main model directly; (2) an existing agent is a 100% fit (a research question → `web-searcher`, checking a claim → `adversarial-reviewer`) → single direct agent call, no prepare+execute — but only when the fit is really 100%, never as a default reflex; (3) otherwise → full delegated run, tiered per the context rule: T1 plain when the task file already carries rich context; T2 (+prepare research) when the task file is thin — current external facts missing from it (or precision/breadth matters); T3 (primary per the context rule + research-backed s2) for second-opinion runs (see Executor tiers under Agent Delegation). Research tasks default to `web-searcher`; they are delegated only when the research task itself is big/context-hungry beyond a single web-searcher run.

**Do NOT spawn when:**
- The work is simple, well-understood, or would take more coordination than doing it directly
- The model can produce a correct result itself without excessive context use — delegation adds overhead, not quality
- The only benefit would be perceived parallelism or "using the machinery" — there is no quota and no obligation to spawn

**Standing exception — research when needed:**
- **Web research** — when external facts matter, the model researches instead of guessing (see Web Research section). Research is the standing exception to "solve it yourself": it is a judgment call, used when needed — the model does not guess facts it can verify online.

(Adversarial verification is NOT mandatory — it is the optional VERIFY block for critical issues, acted-on findings, or on demand, see Agent Delegation.)

### How to spawn

All 4 agents are native opencode subagents, auto-loaded from `.opencode/agents/*.md`:

**Standard flow (tiered):**
1. Write the raw task (`tmp/{NAME}-task.txt`) — PRIOR CONTEXT is a first-class input: state contracts, specs, environment, and expected behaviors explicitly; the executor leans on them. If the task depends on current external facts you cannot state, that is the T2 signal (the prepare supplies them). For T2/T3 also write the prepare task (`tmp/{NAME}-prepare-task.txt`, FOCUS included).
2. **T1 (context rule: rich context only — implementations included when specs/contracts are stated):** assemble WITHOUT research:
   ```bash
   .opencode/tools/assemble-task.sh -a executor -t TYPE -n {NAME} --task tmp/{NAME}-task.txt -o tmp/{NAME}-task-prompt.txt
   ```
   Then go to step 5. The task file's PRIOR CONTEXT is the briefing — write it to carry whatever the executor needs (contracts, specs, and any facts you already researched).
3. **T2 (context rule: thin context — research needed but not made beforehand):** assemble + delegate PREPARE: `assemble-task.sh -a prepare-agent -t prepare -n prepare-{NAME} --task tmp/{NAME}-prepare-task.txt`, then `task(subagent_type="prepare-agent")` → research file `tmp/prepare/{NAME}-research.md`. No gate step needed — the prepare agent self-reviews its research file against the quality contract before delivery and fixes issues it finds. Only if its report flags remaining issues: re-prepare or fix before executing.
4. Assemble the EXECUTOR prompt (injection happens automatically):
   ```bash
   .opencode/tools/assemble-task.sh -a executor -t TYPE -n {NAME} --task tmp/{NAME}-task.txt --research-file tmp/prepare/{NAME}-research.md -o tmp/{NAME}-task-prompt.txt
   ```
   Types: `code` / `review` / `research` (choose by work type). Produces `tmp/{NAME}-task-prompt.txt` with structure: template → RESEARCH DATA → task.
5. Delegate via the `task` tool — pass the file path with a read-and-execute instruction, NOT the full content:
   ```
   task(description="<3-5 words>", prompt="Read this file. Strictly follow instructions there and execute the described task: tmp/{NAME}-task-prompt.txt", subagent_type="executor")
   ```
6. MEDIUM+ severity findings tasks: run the second-opinion flow — T3: primary per the context rule (already done in step 5) + research-backed s2 (one prepare with complementary FOCUS + one s2 executor, own paths — see Second-opinion rules).
7. **OPTIONAL VERIFY block** (critical issues, acted-on findings, or on demand): reviewer first — an `executor` (type `review`) reviews the deliverable and files findings (one run per issue); **if the review produced no MEDIUM+ findings, the VERIFY block ends here** — nothing left to falsify or fix. Otherwise ONE adversarial run per VERIFY block (per issue): it falsifies that issue's review findings AND challenges the reviewer's rejected-non-bug list; on T3 merges it prioritizes the unique findings (STANDALONE — `adversarial-reviewer`). Then the FIX stage (a fresh executor run fixes every CONFIRMED finding with the findings as context), then RE-VERIFY (re-review + re-run the adversarial check on the changed parts; new CONFIRMED findings go back to the fix stage). Cap: 3 fix passes per issue (see VERIFY under Agent Delegation).

**Standalone use of agents outside the flow** (adversarial-reviewer, web-searcher): assemble with their agent name — `assemble-task.sh -a adversarial-reviewer -t review -n ...`.

**Task file contents:** PROJECT, YOUR TASK (KEY FILES, CONTEXT, SCOPE), MUST ANSWER questions, DELIVERABLES paths (unique per agent run). Write `tmp/{NAME}-task.txt`, then assemble. Code tasks get a WRITABLE FILES section listing exactly which source files may be modified. **PRIOR CONTEXT quality matters:** state the module's contracts, specs, environment facts, and expected behaviors explicitly — the executor leans on them; do not expect the research phase to supply what the task file should state.

**Task prompt self-sufficiency (MANDATORY):** All per-task context must live in the task prompt — key files, scope, constraints, questions, research data. Do NOT rely on AGENTS.md or the agent's `.md` as the operating manual for task specifics. The agent gets a self-contained assignment.

**Parallel spawns — DEFAULT to concurrent:** whenever agents are independent, run them in parallel (multiple `task` calls in ONE message) — e.g., a second-opinion run's prepare alongside its primary executor, independent subtasks. Go SEQUENTIAL only when there is a real conflict: agents editing the same file, or a genuine dependency chain (B consumes A's output — e.g., an executor needs its research file first, adversarial needs the deliverable first). Keep parallel batches reasonable (up to ~5); coordination overhead grows with count. Parallel second-opinion runs MUST use their own paths (`-s2-` etc.) — shared paths collide.

**Spawn discipline:**
- **One task per agent.** A subagent executes exactly one task and writes one report. No chained multi-task agents.
- **No two agents edit the same file in parallel** (read overlap is fine). If parallel work needs the same file, split by content or sequence the agents.
- **Respawn discipline:** if an agent fails or produces wrong output, diagnose the root cause (bad prompt? wrong agent? bad research data? environment?), fix it, and re-issue. Maximum 3 respawn attempts per agent (name them `-r2`, `-r3`). After 3 failures, stop and either do the work yourself or discuss the approach with the user.

### Reviewing agent output

- Check the report exists and is non-empty — that's the primary gate
- Read the report's findings and apply them to the main task
- If an agent's output is wrong or incomplete: diagnose (bad prompt? wrong agent? bad research data?), fix the task, and re-spawn with corrections (see spawn discipline above)
- Quality gates by pipeline stage: the research file must pass the prepare agent's quality self-review before execution; MEDIUM+ findings tasks get the second-opinion flow (complementary FOCUS); every delegated task may get the optional VERIFY block for critical issues or on demand (see Quality Practices below).

### General Approach to Serious Issues (findings → fixed & verified)

The default pipeline for resolving serious issues and turning findings into verified outcomes — general, applies to any project and any source of findings (log analysis, code review, user reports, test failures, audits) and to any task with findings to process: bugs, performance problems, security issues, or any work where each finding needs review → falsification → fix → re-verify. For log-derived findings the discovery phase follows the log-analysis protocol below (Phase 1-2: analysis agents per log group + synthesis agent; Phase 3: report-back); for everything else the chain below IS the standard way to resolve issues. **One agent per finding at every stage; parallelize only across independent findings** (respect the parallel-spawn rules above).

The chain (each stage consumes the previous stage's reports as PRIOR CONTEXT — pass the report paths, never flattened summaries):

1. **DISCOVER** (only if findings don't exist yet) — one analysis agent per session/source finds the problems and files severity-labeled findings with evidence (file:line, quoted lines). For log packages: the log-analysis protocol's Phase 1-2 (analysis agents per log group + synthesis agent) and Phase 3 report-back run first.
2. **REVIEW** (one `executor`, type `review`, per finding) — locates the exact root cause in source, proposes a MINIMAL surgical fix (5-15 lines, no heavy refactoring), checks test impact. Verdict: **FIXABLE / EXCLUDE** (with justification).
   - **Git cross-check (MANDATORY in every review brief):** the finding's area must be checked against commits made since the relevant baseline — for report/log-derived findings: the version stated in the report (locate its "Version updated: X.YYY" commit, then `git log <bump-commit>..HEAD --oneline`); otherwise: the last version bump. If the area was already modified: determine whether the existing change covers the observed case or whether a variant/gap remains. Cite the commits. Regression-awareness rules (Quality Practices) apply.
3. **ADVERSARIAL** (one `adversarial-reviewer` per finding — each finding is its own issue, its own VERIFY block; MEDIUM+ only) — falsifies the review's claims: the root-cause attribution AND the fix proposal (would the fix actually work? does the code already handle the case through another path? does the proposal miss a variant? is it minimal and safe?). Verdicts CONFIRMED / REJECTED / WEAKENED per claim. Only surviving reviews proceed to FIX; REJECTED/WEAKENED findings are dropped or downgraded. **If the review produced no MEDIUM+ findings, the chain ends for that finding** — nothing left to falsify or fix.
4. **FIX** (one `executor`, type `code`, per FIXABLE finding) — implements exactly the reviewed-and-adversarially-verified fix. WRITABLE FILES = the exact files. Self-verify: py_compile/syntax of changed files, grep affected tests, targeted test run (never the full suite). Report the diff and the verification result.
5. **POST-FIX REVIEW** (one `executor`, type `review`, per fix) — verifies the applied diff against the original fix design: correctness, minimality, new bugs, test breakage, race conditions. Verdict **APPROVED / NEEDS-FIX**.
6. **POST-FIX ADVERSARIAL** (ONLY if any post-fix review produced MEDIUM+ findings) — ONE `adversarial-reviewer` per issue (a single run on that issue's post-fix findings). All clean → skip.
7. **FINAL FIXES** — apply any confirmed post-fix findings (fresh executor run per finding), then re-review. Report the final picture to the user: finished & skipped issues, verdicts, and (for log-derived bugs) the UI smoke tests per the log-analysis protocol.

Naming: `s1-review-<id>`, `s1-adv-<id>`, `s1-fix-<id>`, `s1-rereview-<id>`, `s1-postadv-<id>` (report paths must be unique per agent run).

Cap: 3 fix passes per issue (each finding is its own issue; see VERIFY). Never batch multiple findings into one fix agent unless they share the same file/flow — then split by file; never let two agents edit the same file in parallel.

**Tier rule in this flow:** review and fix briefs are **T1 (plain)** — the finding evidence, root-cause context, and fix design travel in the task file, which IS the briefing; the agent reads internal codebase facts itself. Research (T2/prepare) enters only when the fix depends on current external facts the task file does not carry. **State the tier explicitly in every brief** (e.g. "TIER: T1 — task file carries rich context; all facts internal"). The synthesis/report stage that produced the findings is where external research (if any) would have been baked in already.

---

## Quality Practices

### Research-file quality (self-reviewed by prepare — no separate gate)

The research-file quality contract lives in the prepare agent's instructions, not in a script: size soft max ~15KB (1–2KB over acceptable, no minimum), every technology covered with per-tech sections, confidence tiers on claims, project policies baked in, source mapping, no raw dumps. The prepare agent self-reviews its file against this contract before delivery (max 2 fix passes) and reports remaining issues explicitly. The main model does NOT check the file — it only acts when the prepare report flags remaining issues (re-prepare or fix before executing). A scripted gate existed in testing and caught real format defects (e.g., a research file missing all confidence tiers), but was removed in favor of instruction-level self-review — content quality is additionally covered by the second-opinion flow at findings tasks and the adversarial finish.

### Verify before claiming (grep first)

Before claiming something is missing, broken, or unimplemented — grep for existing guards, handlers, or implementations first. Search the codebase for the thing you think is absent before reporting it absent. A claim like "there is no validation here" requires a search that confirms it.

### Self-review after non-trivial code

After writing or modifying non-trivial code: re-read your own diff, run the available tests/build/lint, and check edge cases before delivering. Present the result as reviewed, with test results stated. For significant or security-sensitive changes, consider running a quick `adversarial-reviewer` pass for an independent falsification check.

### Adversarial check (part of the optional VERIFY block)

The optional VERIFY block (critical issues, acted-on findings, or on demand — see Agent Delegation) runs as: reviewer → ONE adversarial check per block → FIX → re-verify; **if the review produces no MEDIUM+ findings, the block ends there**. `adversarial-reviewer` runs STANDALONE (no second-opinion pair — second opinions belong to findings/research/review stages, not to the adversarial verification itself). (Routine trivial session work is covered by self-review and tests.) The adversarial reviewer tries to FALSIFY the work: it reads the deliverable with full surrounding context, searches exhaustively for counter-evidence, errors, and missed edge cases, and reports what survives as CONFIRMED issues.

How to use it:
- The review stage comes first: an `executor` (type `review`) examines the deliverable (code written, changes made, or findings reported) and files severity-labeled findings (one run per issue). If the review produced no MEDIUM+ findings, the block ends. Otherwise spawn ONE `adversarial-reviewer` per VERIFY block with that issue's findings and ask it to hunt for bugs, regressions, and unhandled edge cases in the result
- For findings-type outputs: include the issue's findings and ask it to falsify each finding (FP → REJECTED, overstated → WEAKENED with correct severity) AND to challenge the report's "investigated-and-rejected" list — dismissed items can be real bugs
- On T3 merged outputs: tell it which findings are unique to the s2 standpoint vs both-found, and prioritize the unique ones
- Include KEY FILES (the files that were changed / the deliverable), CONTEXT, and MUST ANSWER questions like: "Are there any bugs, edge cases, or regressions in this change? Is the change correct in all call paths?"
- Treat its CONFIRMED findings as real issues — the FIX stage fixes them (a fresh executor run with the findings as additional context)
- Verdict contract: findings labeled CONFIRMED (survived falsification — real issue), WEAKENED, or REJECTED (attempted attack did not survive — not a real issue)
- Re-verify: re-review the changed parts and re-run the adversarial check on them (one `adversarial-reviewer` per issue); any new CONFIRMED findings go back to the fix stage. Cap: 3 fix passes per issue — persistent issues are reported to the user, not looped forever

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
| No report after exit (empty/blank result) | **Resume first, respawn second:** re-invoke the task tool with the same session id (`task_id`) asking it to deliver — the session keeps its context and writes the report. Only if the resume fails, diagnose (bad prompt? missing dependency? environment?) and re-issue. |
| Agent claims success but output wrong | Diagnose why (bad prompt? misunderstood task?). Fix the prompt/task. Re-issue. |
| Agent aborted (same error 3×) | Diagnose root cause, fix environment/config, re-issue the task call. If it fails a 4th time, do the work directly or discuss with the user. |
| 2+ agents fail same env error | STOP respawning. Diagnose environment first. |

---

## Delivery

- Write final results to the user in the session — summaries, reports, files changed, severity-labeled findings
- Clean up temporary task files: `rm -f tmp/*-task-prompt.txt tmp/*-task.txt` (keep reports, logs, memory)
- Save non-trivial discoveries to knowledge (`memory.sh add`) and task state to session (`memory.sh session add`)
