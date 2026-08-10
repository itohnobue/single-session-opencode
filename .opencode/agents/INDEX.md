# Agent Directory (5 agents)

Quick selection reference. All workflow instructions live in `AGENTS.md` — the Agent Delegation section (executor tiers T1/T2/T3, second-opinion rules, optional VERIFY block). Read it before delegating.

| File | Agent | Role |
|------|-------|------|
| prepare-agent.md | Prepare agent | Research generation per task (T2/T3 only). FOCUS parameter = specialist identity; ≤3 queries per tech; one ≤15KB research-data file (soft max, no minimum); quality self-review before delivery; task-context anchoring + provisional traps. |
| executor-high.md | Executor agent (HIGH) | Default executor. T1 (plain — task context is the briefing) or T2/T3 (after prepare: template → RESEARCH DATA → task); second-opinion runs (s2) with complementary-FOCUS research briefings. |
| executor-max.md | Executor agent (MAX) | Same as executor-high, deeper reasoning — use for deep-analysis/investigation research tasks; synthesis and implementation stay on executor-high. |
| adversarial-reviewer.md | Adversarial reviewer | Falsification gate — optional VERIFY block (critical issues, acted-on findings, on demand). Falsifies findings (FP → REJECTED, overstated → WEAKENED), challenges rejected-non-bug lists, prioritizes unique findings on merged s2 outputs; CONFIRMED findings drive fix + re-verify (cap 3 passes). |
| web-searcher.md | Web researcher | Deep-research fallback for the main model when a task needs beyond the prepare budget. |

The one general principle — the context rule (see AGENTS.md): plain is used ONLY when the task file already carries rich context (facts stated in PRIOR CONTEXT or already researched in); when the task file is thin and the task depends on facts it does not carry, research is injected to supply what the context lacks. This applies to every tier. Tiers: **T1 plain** (rich context only; incl. implementations when specs/contracts are stated); **T2 researched** (thin context — external facts missing from the file; or precision/breadth matters); **T3 second-opinion runs** (primary per the context rule + research-backed s2 with complementary FOCUS; no role-only s2). Always merge primary + s2.

Specialist identity = research data: the same task prepared with different FOCUS angles produces different specialists (see AGENTS.md Second-opinion rules).
