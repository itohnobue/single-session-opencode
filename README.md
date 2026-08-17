# Single-Session OpenCode

A single-session agent suite for [OpenCode](https://opencode.ai): the model does the work itself in dialog with you, and calls in specialized subagents when the work is big or context-heavy. No orchestration pipeline. Works with any LLM provider.

## Quick Start

```bash
git clone https://github.com/itohnobue/single-session-opencode
cd single-session-opencode
./install.sh /path/to/your/project   # macOS/Linux
# or: .\install.ps1 C:\path\to\project   (Windows)
```

The installer copies `.opencode/` (agents, tools, templates), `AGENTS.md`, and a minimal `opencode.json` into your project — an existing `AGENTS.md` or `opencode.json` is never overwritten. Open the project with OpenCode and the suite is active.

## Default allowance

The shipped `opencode.json` sets only `permission: allow` — **no model pin**: the model and provider come from your machine's global OpenCode config (`~/.config/opencode/opencode.json`). Edit the project `opencode.json` for a per-machine override. Agent reasoning effort is set per-agent in `.opencode/agents/*.md`.

## How it works

- **One session, one worker** — the model reads, writes, runs, and verifies, all in dialog with you. You can interject or redirect at any moment.
- **Search-first** — when the answer depends on external facts (versions, APIs, docs), the model runs `web_search.sh` instead of guessing. Memory-only answers are the exception.
- **Research-backed delegation** — big/heavy/context-hungry work goes to subagents: `prepare-agent` researches a task's technologies into a full research report + compact digest, an executor does the work with that briefing (the model may also inject its own research directly — same digest + full report scheme, a general input to any executor run). Findings work at MEDIUM+ gets a research-backed second opinion; critical work gets the optional VERIFY block (review → adversarial check → fix → re-verify). Delegation is judgment, not a pipeline.
- **Memory that survives** — two-tier knowledge/session memory via `memory.sh`.

## The 8 agents

`.opencode/agents/` — INDEX.md is the quick reference.

| Agent | Role |
|-------|------|
| `prepare-agent` | Web-researches a task's technologies (≤3 queries per tech), curates a full research report (no size cap) + compact digest (~10KB) the executor prompt carries. `FOCUS:` defines the specialist identity. |
| `executor` | Does the work — plain (task context as briefing) or with a research briefing. All work types: implementation, review, research, deep analysis. |
| `verification-analyst` | Extraction/synthesis/knowledge-harvesting for findings-heavy flows — dedup, tagging, synthesis grid. |
| `adversarial-reviewer-max` | Falsification gate (MAX effort) — CRITICAL (1:1) and HIGH (1:3) finding batches. |
| `adversarial-reviewer-high` | Falsification gate (HIGH effort) — MEDIUM (1:10) finding batches. |
| `web-searcher` | Deep-research fallback — internet research (standards, versions, ecosystems, advisories). |
| `research-analyst` | Structured multi-source research — tech comparisons, literature reviews, market research. |
| `data-researcher` | Dataset research — discovery, collection, quality assessment. |

## Requirements

- [OpenCode CLI](https://opencode.ai)
- At least one LLM provider configured in `~/.config/opencode/opencode.json`
- `uv` (auto-installed if missing — handles Python dependencies for tools)

## License

MIT
