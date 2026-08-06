# Single-Session OpenCode

A single-session agent suite for [OpenCode](https://opencode.ai). All work happens in one session with the operator — the model does the work itself, and decides on its own when to call in a specialist subagent. No orchestration pipeline, no lead, no stage machinery. Works with any LLM provider.

## Why use it

- **One session, one worker** — You work with the model directly. It reads, writes, runs commands, and delivers — all in the current session. You can interject or redirect at any moment.
- **109 specialist agents on demand** — The model handles simple and medium work itself. When a task is serious — domain expertise, real implementation, large independent scope — the model spawns the right subagent from `.opencode/agents/` with its own task and context. Subagents are a tool for the hard stuff, not a pipeline.
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

The installer copies `.opencode/` (agents, tools, templates) into your project and creates `AGENTS.md` with the single-session workflow instructions. If `.opencode/` already exists, it merges new files without overwriting existing ones. Open your project with OpenCode — the workflow is active immediately.

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
    [Subagent?]    Serious work the model shouldn't do alone:
         │         domain expertise, big implementation,
         │         large independent scope, verification?
         │         The model spawns a specialist from
         │         109 agents via the task tool — only
         │         when delegation genuinely helps.
         │         Otherwise keeps working directly
         ▼
     Delivered     Results, reports, and findings in the session;
         │         discoveries saved to memory; tmp/ cleaned
         ▼
    Operator loop  You interject, redirect, ask, or assign the next
                   task — the model responds immediately
```

No lead. No planning pipeline. No verification stages (except the adversarial gate for high-priority work). Just you, the model, and specialists on call — used only when the work is serious enough to need them.

## Key concepts

**Single session** — The operator and the model work together in one session. Tasks are focused and self-contained — sized so the model completes them while you watch. Not orchestrator-scale productions.

**Subagents** — 109 specialist agents in `.opencode/agents/` (INDEX.md has the full categorized directory). The model solves simple and medium work itself. A subagent is spawned only for serious tasks where delegation genuinely helps: domain expertise (deep security review, query optimization), serious implementation work (a real feature or module in a language the specialist knows best), large independent scope (context protection), or verification of high-stakes results. Web research is the standing exception — the model always researches externally rather than guessing. The model picks the agent, writes the task, reads the report, applies the findings. No mandatory pipeline — subagent use is the model's judgment call, with one exception: for high-priority or high-risk work the model must run `adversarial-reviewer` over the result before delivering (see AGENTS.md → Quality Practices).

**Proactive web research** — The core discipline of this suite: before answering anything that touches the external world, the model runs `./.opencode/tools/web_search.sh "query"` (or `web_search.bat` on Windows). Facts, versions, API contracts, docs, alternatives, breaking changes — all researched, never guessed. For deep multi-query research the model may delegate to the `web-searcher` agent instead.

**Memory** — `memory.sh` (or `memory.bat` on Windows) provides two tiers: **knowledge** (permanent facts: architecture, gotchas, patterns, configs) and **session** (current task state: todos, progress, blockers). Facts learned this session persist across sessions and machines.

**Temporary files** — Reports, logs, and task prompts go to `tmp/` relative to the repository root. Task prompts are assembled with `assemble-task.sh`, which injects absolute paths automatically.

## Requirements

- [OpenCode CLI](https://opencode.ai)
- At least one LLM provider configured in `~/.config/opencode/opencode.json`
- `uv` (auto-installed if missing — handles Python dependencies for tools)

## License

MIT
