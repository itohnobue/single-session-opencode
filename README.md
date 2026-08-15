# Single-Session OpenCode

A single-session agent suite for [OpenCode](https://opencode.ai). All work happens in one session with the operator — the model does the work itself, and decides on its own when to call in a subagent. No orchestration pipeline, no lead, no stage machinery. Works with any LLM provider.

## Default allowance

The repo ships with a minimal `opencode.json`: `permission: allow` and **no model pin** — the model and provider come from your machine's global OpenCode config (`~/.config/opencode/opencode.json`). Agent reasoning effort is set per-agent in `.opencode/agents/*.md` (`reasoningEffort: max/high`); the main session uses whatever the default model's options say (typically `max`). Edit `opencode.json` locally if you need a per-machine override — the committed version stays minimal by design.

## Why use it

- **One session, one worker** — You work with the model directly. It reads, writes, runs commands, and delivers — all in the current session. You can interject or redirect at any moment.
- **Tiered executor pipeline** — 4 lean agents instead of a wall of static specialists, governed by one context rule: plain execution (T1) when the task file already carries rich context; researched execution (T2) when the file is thin and needs current external facts — `prepare-agent` researches them into one research-data file, an executor does the work with that briefing; second-opinion runs (T3) pair a context-rule primary with a complementary-FOCUS research-backed second opinion. Specialist identity comes from the research data, not from canned `.md` personas — fresher and per-task.
- **Search-first for external facts** — When the answer depends on external facts — specs, docs, APIs, best practices, recent changes — the model defaults to a quick `web_search.sh` even when it mostly knows the answer. Memory-only is the exception: facts already in the task file or trivially stable ones.
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
    [Research?]    Depends on external facts?
          │         It runs web_search.sh — facts, versions, docs.
          │         Doesn't guess what it can check
         ▼
    [Delegate?]    Big/heavy or context-hungry work, or a task that
         │         touches current external facts? The model runs the
         │         tiered pipeline — T1 plain (the task's own context
         │         is the briefing), T2 researched (prepare-agent
         │         researches → executor works with the briefing),
         │         T3 second opinion. Otherwise works directly
         ▼
     Delivered     Results, reports, and findings in the session;
         │         discoveries saved to memory; tmp/ cleaned
         ▼
    Operator loop  You interject, redirect, ask, or assign the next
                   task — the model responds immediately
```

No lead. No planning pipeline. No mandated verification stages — verification is the optional VERIFY block, run for critical/high-risk work, acted-on findings, or on demand. Just you, the model, and the pipeline on call — used only when the work is serious enough to need it.

## Key concepts

**Single session** — The operator and the model work together in one session. Tasks are focused and self-contained — sized so the model completes them while you watch. Not orchestrator-scale productions.

**The 4 agents** (`.opencode/agents/`, INDEX.md is the quick reference):

| Agent | Role |
|-------|------|
| `prepare-agent` | Researches a task (T2/T3 runs only): every technology it touches, ≤3 queries per tech, one ≤15KB research-data file. `FOCUS:` parameter defines the specialist identity. |
| `executor` | Does the work — T1 plain (the task file's own context is the briefing) or T2/T3 after prepare (research data as the briefing). All work types: implementation, review, research, deep analysis. |
| `adversarial-reviewer` | Falsification gate for the optional VERIFY block — falsifies findings, challenges rejected-non-bug lists, reports CONFIRMED issues. |
| `web-searcher` | Deep-research fallback when a task needs research beyond the prepare budget. |

**Delegation** — The model solves simple and medium work itself, and applies one context rule when delegating: **plain** when the task file already carries rich context (T1 — specs, contracts, and expected behaviors stated in PRIOR CONTEXT), **researched** when the file is thin and depends on current external facts (T2 — `prepare-agent` researches → `assemble-task.sh` injects the research into the task prompt: template → RESEARCH DATA → task → the executor does the work). Findings/analysis tasks at MEDIUM+ get a **second opinion** (T3): a context-rule primary plus a complementary-FOCUS research-backed second run — results are always merged, never replaced. The optional **VERIFY block** — reviewer → ONE adversarial check per block → fix → re-verify, one block per issue, capped at 3 fix passes per issue; if the review produces no MEDIUM+ findings, verification ends — runs for critical/high-risk work, acted-on findings, or on demand. No mandatory pipeline — delegation is the model's judgment call.

**Search-first for external facts** — When the answer depends on external facts — versions, API contracts, docs, alternatives, breaking changes — the model runs a quick `./.opencode/tools/web_search.sh "query"` (or `web_search.bat` on Windows) as the default, even when it mostly knows the answer: the tool is cheap, and guessing verifiable facts is the failure mode. Memory-only is the exception for facts already in the task file or trivially stable. For deep multi-query research the model may delegate to the `web-searcher` agent instead.

**Memory** — `memory.sh` (or `memory.bat` on Windows) provides two tiers: **knowledge** (permanent facts: architecture, gotchas, patterns, configs) and **session** (current task state: todos, progress, blockers). Facts learned this session persist across sessions and machines.

**Temporary files** — Reports, logs, and task prompts go to `tmp/` relative to the repository root. Task prompts are assembled with `assemble-task.sh`, which injects absolute paths automatically.

## Requirements

- [OpenCode CLI](https://opencode.ai)
- At least one LLM provider configured in `~/.config/opencode/opencode.json`
- `uv` (auto-installed if missing — handles Python dependencies for tools)

## License

MIT
