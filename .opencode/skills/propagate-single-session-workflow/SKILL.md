---
name: propagate-single-session-workflow
description: Propagate ALL workflow changes from single-session-opencode (source of truth — AGENTS.md, agents, templates, tools, plugins, workflow skills) to every repo that carries the single-session workflow. Two destination types: full-payload destinations (repos that run the single-session pipeline directly, currently working_dir) receive the entire workflow; orchestrator destinations receive the adapted single-session-workflow skill plus the shared handoff skill and the shared handoff-compaction plugin. NEVER use automatically — only when the user explicitly commands propagation.
disable-model-invocation: false
argument-hint: []
---

# Propagate Single-Session Workflow to All Repos

**NEVER LAUNCH WITHOUT AN EXPLICIT USER COMMAND (MANDATORY GATE):** This skill runs ONLY when the user explicitly commands propagation in the current session (e.g. "run the propagation skill" / "propagate the workflow"). A workflow-file change is NEVER itself a trigger — no automatic invocation, no self-initiated run as a follow-up, no starting the per-repo sequence "since it's pending". If propagation seems needed, state that it is due and wait for the user's explicit command; the user may prefer to propagate later or not at all.

Synchronize ALL single-session workflow content from `single-session-opencode` (the authoritative version) to every repo that carries it. The workflow is defined by the whole set: `AGENTS.md` + `.opencode/agents/*.md` + `.opencode/templates/*.txt` + `.opencode/tools/*` + `.opencode/plugin/*` + workflow skills (`.opencode/skills/handoff/SKILL.md`). Any of these can change; every destination receives the parts relevant to its type. This is a careful, diligent operation — every step is verified, and on completion a full cross-repo check is performed.

## Destination Types

The single-session workflow reaches repos in TWO forms, by destination type:

1. **Full-payload destinations** — repos that run the single-session pipeline themselves (they execute these exact agents, templates, tools, plugins, and workflow skills). They receive the ENTIRE workflow:
   - the `AGENTS.md` workflow section (below their project-specific section)
   - `.opencode/agents/*.md`, `.opencode/templates/*.txt`, `.opencode/tools/*` (excluding `__pycache__`), byte-identical
   - `.opencode/plugin/*` (local plugins — `fix-prompt.js` and `handoff-compaction.js`, both with the dual V1/V2 entrypoint), byte-identical
   - workflow skills — `.opencode/skills/handoff/SKILL.md`, byte-identical
   - **Current member:** working_dir
2. **Skill destinations (orchestrator adaption)** — repos with their own pipeline that carry the single-session workflow as an embedded skill, adapted to the orchestrator context. They receive the transformed `single-session-workflow` SKILL.md plus the shared `handoff` skill and the shared `.opencode/plugin/handoff-compaction.js`.
   - **Current members:** orchestrator-opencode, fallout2-ce-extended, hpgl-reborn, pylasdev-reborn, arcaios, arcaandroid, pe_mac, peoutlook

Orchestrator's own pipeline (agents, templates, tools, its AGENTS.md) is NEVER touched — orchestrator has its own 10-agent pipeline; our 7 agents and tools are not used there. The shared `handoff` skill and the shared `handoff-compaction.js` plugin are the two exceptions: both are delivered to orchestrator destinations too (their continuation wiring invokes the skill; the plugin owns compaction there as well). Full-payload destinations run the single-session pipeline directly, so their agents/templates/tools/plugins/workflow skills must stay in sync with the source — this is a mandatory part of their propagation, not an optional extra.

## Source of Truth

- **`single-session-opencode`** — the authoritative version. All propagation originates here. Run this skill from the single-session-opencode repo root; `AGENTS.md` is the source file.

## The Transformation (AGENTS.md → single-session-workflow skill)

The skill is NOT a verbatim copy of AGENTS.md — it is adapted to the orchestrator context. **Adaptation rules (MANDATORY):**
1. **Frontmatter** (fixed, preserved verbatim):
   ```yaml
   ---
   name: single-session-workflow
   description: "Switch to single-session mode. The main model stops acting as the orchestrator lead and becomes the single-session worker: it does the work itself in dialog with the user, delegating only per the tiered executor pipeline rules below. Use when the user wants direct single-session behavior instead of the orchestration workflow."
   disable-model-invocation: false
   argument-hint: []
   ---
   ```
2. **Title swap:** replace the `# Single-Session OpenCode` title with:
   ```
   # Single-Session Workflow
   ```
3. **MODE SWITCH block** immediately after the title:
   ```
   **MODE SWITCH — MANDATORY:** From now on strictly follow these instructions instead of your own AGENTS.md. You are no longer an orchestrator — you are the single-session workflow: the main session is the primary worker, you do the work yourself in dialog with the user, and delegation is the exception. Do not spawn the planner pipeline, do not delegate stage agents, do not run orchestration stages.
   ```
4. **Executor wording** — these spots:
   - Agents table, executor row → `Executes the assembled task (T1: task context is the briefing; T2/T3: template → RESEARCH DATA → task). Post-fix review is NOT its job — that is `postfix-reviewer`'s. No research of its own.`
   - The flow, step 4 EXECUTE → `spawn `executor` for the work.` (drop the work-type parenthetical)
   - The `postfix-reviewer` agent row (post-fix review ONLY, read-only) propagates verbatim — both repos carry the identical agent.
   - (The old `### Executor selection` section no longer exists in the source — nothing to adapt.)
5. **Adversarial-reviewer agent resolution** — the source has a SINGLE `adversarial-reviewer` agent (the one distinct quality gate). Propagate the row verbatim; each target must carry the single `adversarial-reviewer.md` agent so the name resolves — if a target's copy is still split, merge it into the single agent.
6. **Log-analysis references** (orchestrator has the `/process-report-logs` skill; our AGENTS.md refers to a generic upstream log-analysis stage) — two spots:
   - T3 workflow intro (now under `## Delegation Operations` → "The T3 tier's full workflow"): `for log-derived findings they are produced first by the /process-report-logs skill (Phase 1-2: analysis agents per log group + synthesis agent, Phase 3: report-back)` (replaces the "an upstream log-analysis stage produces them first" clause)
   - FINAL FIXES step (same chapter): `(for log-derived bugs) the UI smoke tests per the log-analysis skill` (replaces "the project's UI smoke tests")
7. **Everything else** propagates verbatim from the current `AGENTS.md` (from `## Temporary Files` down) — including workflow changes that arrive after the skill was last synced (VERIFY structure, task splitting by volume, convergence rule, Web Research stance, section renames, MEDIUM+/HIGH+ gates).

## Full-Payload Destinations (AGENTS.md splice + runtime files)

For **full-payload destinations** (currently working_dir), there is no skill — they receive the complete workflow:
- **AGENTS.md workflow section** (from `## Temporary Files` down) spliced from the source
- **Runtime files** — `.opencode/agents/*.md`, `.opencode/templates/*.txt`, `.opencode/tools/*`, `.opencode/plugin/*`, and workflow skills (`.opencode/skills/handoff/SKILL.md`) copied from the source (byte-identical). This is how pipeline changes (assemble-task.sh, inject-research.sh, coordination templates, agent behavior, the dual V1/V2 plugin) reach these repos.
- **Never delete or skip destination-only files** (personal skills like `update-server`, `vespa-*`, `upload-to-aoizora`; `current_session`) — only copy/overwrite files that exist in the source. The suite owns `.opencode/plugin/fix-prompt.js` and `.opencode/plugin/handoff-compaction.js`; **remove any legacy `.opencode/plugins/fix-prompt.js`** in the destination (V2 discovers both `plugin/` and `plugins/`, and a duplicate plugin id fails to load).

## Target Repos

| Repo | Path | Branch | Type | What to update | Git handling |
|------|------|--------|------|----------------|--------------|
| working_dir | `../working_dir` | main | full-payload | `AGENTS.md` workflow section (from `## Temporary Files` down) + `.opencode/agents/*.md` + `.opencode/templates/*.txt` + `.opencode/tools/*` (excl. `__pycache__`) + `.opencode/plugin/*` + `.opencode/skills/handoff/SKILL.md`; keep destination-only files | tracked → commit + push (aoizora.ru) |
| orchestrator-opencode | `../orchestrator-opencode` | main | skill | `skills/single-session-workflow/SKILL.md` + `skills/handoff/SKILL.md` (tracked; `.opencode/skills/single-session-workflow` and `.opencode/skills/handoff` symlink to them) + `.opencode/plugin/handoff-compaction.js` | tracked → commit + push (aoizora.ru) |
| fallout2-ce-extended | `../fallout2-ce-extended` | main | skill | `.opencode/skills/single-session-workflow/SKILL.md` + `.opencode/skills/handoff/SKILL.md` + `.opencode/plugin/handoff-compaction.js` | machine-local (`.opencode/` gitignored) — write only, no git steps |
| hpgl-reborn | `../hpgl-reborn` | master | skill | `.opencode/skills/single-session-workflow/SKILL.md` + `.opencode/skills/handoff/SKILL.md` + `.opencode/plugin/handoff-compaction.js` | machine-local — write only, no git steps |
| pylasdev-reborn | `../pylasdev-reborn` | master | skill | `.opencode/skills/single-session-workflow/SKILL.md` + `.opencode/skills/handoff/SKILL.md` + `.opencode/plugin/handoff-compaction.js` | machine-local — write only, no git steps |
| arcaios | `/Volumes/work/pe/arcaios` | main | skill | `.opencode/skills/single-session-workflow/SKILL.md` + `.opencode/skills/handoff/SKILL.md` + `.opencode/plugin/handoff-compaction.js` | tracked → commit + push |
| arcaandroid | `/Volumes/work/pe/arcaandroid` | main | skill | `.opencode/skills/single-session-workflow/SKILL.md` + `.opencode/skills/handoff/SKILL.md` + `.opencode/plugin/handoff-compaction.js` | tracked → commit + push |
| pe_mac | `/Volumes/work/pe/pe_mac` | casa **and** stable | skill | `.opencode/skills/single-session-workflow/SKILL.md` + `.opencode/skills/handoff/SKILL.md` + `.opencode/plugin/handoff-compaction.js` | tracked → commit + push on BOTH branches |
| peoutlook | `/Volumes/work/pe/peoutlook` | stable | skill | `.opencode/skills/single-session-workflow/SKILL.md` + `.opencode/skills/handoff/SKILL.md` + `.opencode/plugin/handoff-compaction.js` | tracked → commit + push |

**Adding a destination:** a repo that runs the single-session pipeline itself joins the full-payload type; a repo with its own pipeline that embeds the workflow as a skill joins the skill type. Update the table with its path, branch, and type — the per-repo sequence below applies as-is.

**Always verify each repo's actual branch with `git -C <repo> branch --show-current` — never assume the branch.** The machine-local repos may be missing or unmounted (e.g., `/Volumes/work/pe/` not mounted) — check existence first; if a repo is absent, note it and continue with the rest.

**pe_mac propagates to TWO branches: `casa` and `stable`.** Check `git -C pe_mac branch -a` to confirm which exist; propagate to `stable` only if it exists. Apply the full per-repo sequence (pull → propagate → check → commit → push) on `casa`, then repeat on `stable`.

## Core Rules (MANDATORY)

### 1. No Contamination
Never propagate single-session-opencode's **project-specific** content into other repos. This includes:
- This repo's own project-specific skills (`propagate-single-session-workflow`), `knowledge.md`, `session.md`, `.opencode/current_session` — workflow skills (`.opencode/skills/handoff/`) ARE payload and propagate
- Anything unique to single-session-opencode's setup
- Agents / templates / tools / workflow skills go ONLY to full-payload destinations; **skill destinations** run their own pipeline and never receive them. The shared `handoff-compaction.js` plugin is the exception — it travels to both destination types.

### 2. No Destruction
Never delete, overwrite, or modify a target repo's **project-specific** content:
- Full-payload destinations' AGENTS.md project-specific section (everything above the first `## Temporary Files` heading) — preserve byte-identical
- Destination-only files (personal skills, `current_session`) — never delete or skip-copy; only copy/overwrite files that exist in the source. The suite-owned plugins live at `.opencode/plugin/fix-prompt.js` and `.opencode/plugin/handoff-compaction.js`; a legacy `.opencode/plugins/fix-prompt.js` must be removed (duplicate id fails to load in V2)
- Skill destinations' own AGENTS.md, agents, templates, tools, knowledge.md, session.md, per-repo skills
- The `single-session-workflow` skill frontmatter — it is fixed, never regenerated

### 3. Selective Propagation
Propagate ONLY the single-session workflow content:
- **Full-payload destinations:** the AGENTS.md workflow section + agents + templates + tools + plugins + workflow skills (the full payload, byte-identical from source)
- **Skill destinations:** the `single-session-workflow` SKILL.md body + the shared handoff skill (orchestrator-opencode: tracked `skills/handoff/SKILL.md` + `.opencode/skills/handoff` symlink; all other skill destinations: `.opencode/skills/handoff/SKILL.md`) + `.opencode/plugin/handoff-compaction.js` (create `.opencode/plugin/` if absent)
Never:
- Touch skill destinations' own pipeline files, even if they look stale
- Delete a target file that may be project-specific
- Force-add optional files a target deliberately doesn't have
- Skip-copy a source file in a full-payload destination — a skipped file means the destination silently keeps a stale version (this was the root cause of the tools/templates drift)

## Per-Repo Sequence

For each target, in sequence (working_dir and orchestrator-opencode first — they are the primary consumers — then the machine-local repos, then the pe repos):

### Step 1: Inspect
```bash
ls -d <repo-path> || { echo "REPO MISSING — skip"; continue; }
git -C <repo> branch --show-current
git -C <repo> status --short
```
- Note the branch
- Note any **pre-existing uncommitted changes** — these must NOT be touched or committed

### Step 2: Pull (tracked repos only)
```bash
git -C <repo> pull
```
- Pull the current branch (do not assume `main`)
- If the tree is dirty, pull may fail — handle carefully, never force

### Step 3: Build the propagated file
**Full-payload destinations (AGENTS.md splice + runtime files):**
- Keep everything above the first `## Temporary Files` heading byte-identical
- Replace from `## Temporary Files` to EOF with source AGENTS.md from `## Temporary Files` to EOF
- Copy `.opencode/agents/*.md`, `.opencode/templates/*.txt`, `.opencode/tools/*` (excl. `__pycache__`), `.opencode/plugin/*`, and `.opencode/skills/handoff/SKILL.md` from source → target. Remove any legacy `.opencode/plugins/fix-prompt.js` in the target. **Drift guard (MANDATORY):** before overwriting any file that differs from the source, check whether the target's version is a LOCAL change not yet in single-session (compare `git -C <target> log -1 -- <file>` vs source; or diff the file — if the target has content absent from the source, it may be a destination-first change). If the target version is newer than the source (work-in-progress), do NOT overwrite — report it and stop for a decision. If the source is newer (or the file matches), overwrite.
- Verify the seam: the project section is untouched, the workflow section matches the source exactly, and every payload file matches the source byte-for-byte
- **Do NOT delete destination-only files** (personal skills, `current_session`); DO remove any legacy `.opencode/plugins/fix-prompt.js` (the suite plugins now live at `.opencode/plugin/`)

**Skill destinations (orchestrator adaption):**
- Apply the Transformation rules to source AGENTS.md → the SKILL.md body
- If the target file's frontmatter differs from the fixed frontmatter (rule T1), flag it — do NOT silently rewrite
- Copy the shared handoff skill from source `.opencode/skills/handoff/SKILL.md` — orchestrator-opencode: tracked `skills/handoff/SKILL.md` + create the `.opencode/skills/handoff` symlink (→ `../../skills/handoff`); all other skill destinations: `.opencode/skills/handoff/SKILL.md`. Never ship an R3 reference without the skill.
- Copy the shared plugin `.opencode/plugin/handoff-compaction.js` from source into the target's `.opencode/plugin/` (create the directory if absent). The plugin's template mirrors the handoff skill, so deliver both in the same run. Leave `fix-prompt.js` and the target's own plugins untouched.
- If the target tracks `.opencode/` in git, ensure `.opencode/handoff/` is gitignored — the plugin writes per-session handoff files there at runtime.

### Step 4: Write + verify (per repo)
- Write the propagated content to the target path
- `git diff` the target — verify ONLY the intended file changed
- Verify the project-specific content is byte-identical to before
- Verify the propagated content matches the transformation spec (md5/diff against the built file)

### Step 5: Commit (tracked repos only)
```bash
git -C <repo> add <only the propagated file>
git -C <repo> commit -m "sync: single-session workflow from single-session-opencode"
```
- Stage ONLY the propagated file
- NEVER stage pre-existing dirty changes (knowledge.md, session.md, tmp/, etc.)
- NEVER use `git add -A` if the tree has pre-existing changes

### Step 6: Push (tracked repos only)
```bash
git -C <repo> push
```

## Global Final Check (after ALL repos done)

1. For every skill destination, verify the SKILL.md content is **identical across all 8 copies** (md5 comparison): orchestrator-opencode, fallout2-ce-extended, hpgl-reborn, pylasdev-reborn, arcaios, arcaandroid, pe_mac (both branches), peoutlook — and that the shared handoff `SKILL.md` is byte-identical to the source in every destination (skill + full-payload)
2. Verify `.opencode/plugin/handoff-compaction.js` is present and byte-identical to the source in **every** destination (skill + full-payload). A missing plugin means that repo silently keeps native compaction.
3. Verify each SKILL.md matches the transformation spec: frontmatter fixed, MODE SWITCH present, executor wording adapted, adversarial-reviewer naming matches the source (single agent), `/process-report-logs` references in place, and the body matches current source AGENTS.md (from `## Temporary Files` down) except the allowed adaptations
4. For every full-payload destination, verify the AGENTS.md workflow section (from `## Temporary Files` down) matches the source byte-for-byte, its project section is untouched, AND every payload file (`agents/*.md`, `templates/*.txt`, `tools/*`, `plugin/*`, `skills/handoff/SKILL.md`) matches the source byte-for-byte — any payload drift means the propagation was incomplete
5. Run `tmp/uv/uv run --no-project .opencode/tools/verify_propagation.py` — the orchestrator shared-payload check; exit 0 required (1 = drift, 2 = absent destination, never a pass)
6. For each tracked repo, run `git log --oneline -1` to confirm the sync commit landed — for pe_mac, on both branches
7. Confirm no target has project-specific content damaged

## Working Notes

- `single-session-opencode` itself is the source — do not modify it during propagation
- **Shared tools + plugin:** `single-session-opencode` is the source of truth for the shared tools, `plugin/fix-prompt.js`, `plugin/handoff-compaction.js`, `templates/severity-guide.txt`, and `agents/prepare-agent.md` (see the local `sync-shared-tools` skill). orchestrator-opencode normally receives those via that sync; this skill also delivers `handoff-compaction.js` to every destination (skill and full-payload) so no repo is left on native compaction. Both paths copy from the same source, so the double delivery is idempotent — never let the sources diverge. The plugin's 8-section template mirrors `.opencode/skills/handoff/SKILL.md`: propagate the two together in one run.
- The pe repos may live on a different remote (git.privacyeverywhere.net) — push uses each repo's own origin, no cross-remote assumptions
- Skill files load at opencode startup — after propagation, the new skill content takes effect on the target's next opencode session
- If a step fails or a repo has conflicts: STOP, diagnose, fix before proceeding — do not skip or force
- Work carefully and diligently; verify every step. Errors here propagate to 9 repos.
- **Why full-payload:** AGENTS.md-only propagation previously let tools/templates drift (e.g. assemble-task.sh missing `--research-report`); the full-payload copy plus the drift guard exists to prevent that. When making ANY workflow change in single-session (AGENTS.md, agents/templates/tools, or plugins), ALWAYS flag to the user that a propagation is due — but per the mandatory gate at the top, the skill itself launches only on the user's explicit command.
