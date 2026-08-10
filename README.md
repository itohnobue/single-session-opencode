# Single-Session OpenCode

A single-session agent suite for [OpenCode](https://opencode.ai). All work happens in one session with the operator — the model does the work itself, and decides on its own when to call in a subagent. No orchestration pipeline, no lead, no stage machinery. Works with any LLM provider.

## Why use it

- **One session, one worker** — You work with the model directly. It reads, writes, runs commands, and delivers — all in the current session. You can interject or redirect at any moment.
- **Prepare + execute pipeline** — 5 lean agents instead of a wall of static specialists. For a serious task, `prepare-agent` researches it (best practices, domain knowledge, specialist advice) into one research-data file; an executor then does the work with that briefing. Specialist identity comes from the research data, not from canned `.md` personas — fresher and per-task.
- **Proactive web research** — Whenever external knowledge is needed — specs, docs, APIs, best practices, recent changes — the model researches first via `web_search.sh` before answering or guessing. Research is the default, not an afterthought.
- **Memory that survives** — Two-tier knowledge/session memory via `memory.sh`. Facts learned this session are available next session.
- **Everything included** — Agents, tools, templates, install scripts. Copy the suite into any project with one command.

## Quick Start

```bash
git clone https://github.com/itohnobue/single-session-opencode
cd single-session-opencode
./install.sh /path/to/your/project   # macOS/Linux
# or: .\install.ps1 C:\path\to\project   (Windows)
```

The installer copies `.opencode/` (agents, tools, templates) into your project and creates `AGENTS.md` with the single-session workflow instructions. If `.opencode/` already exists, suite files are synchronized to the current version: `.opencode/agents/` is suite-owned (agent definitions not shipped by the suite are removed, all others updated), tools and templates are updated, and files you created yourself outside `agents/` are kept. An existing `AGENTS.md` is never overwritten — when upgrading from an old suite version, replace or merge it manually. Open your project with OpenCode — the workflow is active immediately.

## How it works

```
You ask: "Add dark mode" or "What's the best way to store these tokens?"
         │
         ▼
    Direct work    The model does the work itself: reads code, writes
         │         code, runs commands, verifies results
         ▼
    [Research?]    Needs external info? The model runs web_search.sh
         │         FIRST, proactively — specs, docs, best practices,
         │         recent changes. Never guesses when it can check
         ▼
    [Delegate?]    Big/heavy or context-hungry work, or a task that
         │         needs research to solve it? The model runs the
         │         prepare + execute pipeline: prepare-agent
         │         researches → executor does the work with the
         │         briefing. Otherwise keeps working directly
         ▼
     Delivered     Results, reports, and findings in the session;
         │         discoveries saved to memory; tmp/ cleaned
         ▼
    Operator loop  You interject, redirect, ask, or assign the next
                   task — the model responds immediately
```

No lead. No planning pipeline. No mandated verification stages — verification is the optional VERIFY block, run for critical/high-risk work or on demand. Just you, the model, and the pipeline on call — used only when the work is serious enough to need it.

## Key concepts

**Single session** — The operator and the model work together in one session. Tasks are focused and self-contained — sized so the model completes them while you watch. Not orchestrator-scale productions.

**The 5 agents** (`.opencode/agents/`, INDEX.md is the quick reference):

| Agent | Role |
|-------|------|
| `prepare-agent` | Researches a task: every technology it touches, ≤3 queries per tech, one ≤15KB research-data file. `FOCUS:` parameter defines the specialist identity. |
| `executor-high` / `executor-max` | Do the work after prepare, using the research data as their briefing. HIGH = default; MAX = deep-analysis/investigation research tasks. |
| `adversarial-reviewer` | Falsification gate for the optional VERIFY block — tries to break the deliverable, reports CONFIRMED issues. |
| `web-searcher` | Deep-research fallback when a task needs research beyond the prepare budget. |

**Delegation** — The model solves simple and medium work itself. A delegated task runs: **prepare** (research generation) → **assemble** (`assemble-task.sh` injects the research data into the task prompt: template → RESEARCH DATA → task) → **execute** (the executor reads the file and does the work). Second-opinion runs (findings/analysis tasks) use a complementary FOCUS and their own paths. The optional **VERIFY block** — adversarial check → fix → re-verify, capped at 3 fix passes — runs for critical/high-risk work or on demand. No mandatory pipeline — delegation is the model's judgment call.

**Proactive web research** — The core discipline of this suite: before answering anything that touches the external world, the model runs `./.opencode/tools/web_search.sh "query"` (or `web_search.bat` on Windows). Facts, versions, API contracts, docs, alternatives, breaking changes — all researched, never guessed. For deep multi-query research the model may delegate to the `web-searcher` agent instead.

**Memory** — `memory.sh` (or `memory.bat` on Windows) provides two tiers: **knowledge** (permanent facts: architecture, gotchas, patterns, configs) and **session** (current task state: todos, progress, blockers). Facts learned this session persist across sessions and machines.

**Temporary files** — Reports, logs, and task prompts go to `tmp/` relative to the repository root. Task prompts are assembled with `assemble-task.sh`, which injects absolute paths automatically.

## Requirements

- [OpenCode CLI](https://opencode.ai)
- At least one LLM provider configured in `~/.config/opencode/opencode.json`
- `uv` (auto-installed if missing — handles Python dependencies for tools)

## License

MIT
