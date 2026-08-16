# Agent Directory (8 agents)

Quick selection reference. All workflow instructions live in `AGENTS.md` — the Agent Delegation section (executor tiers T1/T2/T3, task splitting by volume, second-opinion rules, optional VERIFY block). Read it before delegating.

| File | Agent | Role |
|------|-------|------|
| prepare-agent.md | Prepare agent | Research generation per task (T2/T3 only). FOCUS parameter = specialist identity; ≤3 queries per tech; one ≤15KB research-data file (soft max, no minimum); quality self-review before delivery; task-context anchoring + provisional traps. |
| executor.md | Executor agent | The single executor for all tiers and work types (implementation, review, research, second-opinion s2). T1 (plain — task context is the briefing) or T2/T3 (after prepare: template → RESEARCH DATA → task); second-opinion runs (s2) with complementary-FOCUS research briefings. |
| verification-analyst.md | Verification analyst | Extraction + synthesis + knowledge harvesting for findings-heavy flows (T3 merges, multi-report audits, VERIFY blocks): dedups/tags findings (both-found/single-found, PRIOR_FIX_ATTEMPT), routes investigated-and-rejected items into adversarial batches, compiles the synthesis grid (severity challenges, mechanism categories, FIX determination), harvests patterns into knowledge.md. |
| adversarial-reviewer-max.md | Adversarial reviewer (MAX) | Falsification gate — optional VERIFY block. Max reasoning effort; for CRITICAL (1:1) and HIGH (1:3) finding batches. Falsifies findings (FP → REJECTED, overstated → WEAKENED), challenges rejected-non-bug lists, prioritizes unique findings on merged s2 outputs; CONFIRMED findings drive fix + re-verify (convergence rule — loop continues while CONFIRMED HIGH+ remain). |
| adversarial-reviewer-high.md | Adversarial reviewer (HIGH) | Falsification gate — optional VERIFY block. High reasoning effort; for MEDIUM (1:10) finding batches — contained-impact findings where MAX effort's token cost is not justified. Same methodology and verdict contract as MAX. |
| web-searcher.md | Web researcher | Deep-research fallback for the main model when a task needs beyond the prepare budget. Internet research (standards, formats, versions, ecosystems, advisories). |
| research-analyst.md | Research analyst | Structured multi-source research — analysis/synthesis of gathered material (tech comparisons, literature reviews, market research). Source evaluation, confidence tiers, counter-evidence discipline. |
| data-researcher.md | Data researcher | Dataset research — data discovery, collection, quality assessment, pattern mining. Data-quality and source-quality gates, graduated confidence. |

The one general principle — the context rule (see AGENTS.md): plain is used ONLY when the task file already carries rich context (facts stated in PRIOR CONTEXT or already researched in); when the task file is thin and the task depends on facts it does not carry, research is injected to supply what the context lacks. This applies to every tier. Tiers: **T1 plain** (rich context only; incl. implementations when specs/contracts are stated); **T2 researched** (thin context — external facts missing from the file; or precision/breadth matters); **T3 second-opinion runs** (primary per the context rule + research-backed s2 with complementary FOCUS; no role-only s2). Always merge primary + s2.

Specialist identity = research data: the same task prepared with different FOCUS angles produces different specialists (see AGENTS.md Second-opinion rules).
