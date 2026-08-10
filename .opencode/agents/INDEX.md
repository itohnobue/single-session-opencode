# Agent Directory (5 agents)

Quick selection reference. All workflow instructions live in `AGENTS.md` — the Agent Delegation section (when to delegate, prepare → assemble → execute, second-opinion rules, optional VERIFY block). Read it before delegating.

| File | Agent | Role |
|------|-------|------|
| prepare-agent.md | Prepare agent | Research generation per task. FOCUS parameter = specialist identity; ≤3 queries per tech; one ≤15KB research-data file (soft max, no minimum); quality self-review before delivery. |
| executor-high.md | Executor agent (HIGH) | Default executor. Runs AFTER prepare: reads the assembled prompt (template → RESEARCH DATA → task), applies the briefing, executes, reports. |
| executor-max.md | Executor agent (MAX) | Same as executor-high, deeper reasoning — use for deep-analysis/investigation research tasks; synthesis and implementation stay on executor-high. |
| adversarial-reviewer.md | Adversarial reviewer | Falsification gate — part of the optional VERIFY block (critical issues or on demand). Its CONFIRMED findings drive the fix stage + re-verify loop (cap 3 passes). |
| web-searcher.md | Web researcher | Deep-research fallback for the main model when a task needs beyond the prepare budget. |

Specialist identity = research data: the same task prepared with different FOCUS angles produces different specialists (see AGENTS.md Second-opinion rules).
