Your work is not finished. Read and execute the below with ultrathink.

## Original Implementation Plan

**IMPORTANT**: Before proceeding, review the original plan you are implementing:
@{{PLAN_FILE}}

This plan contains the full scope of work and requirements. Ensure your work aligns with this plan.

---

## Round Re-anchor (REQUIRED FIRST STEP)

Before writing code:
- Re-read @{{PLAN_FILE}}
- Re-read @{{GOAL_TRACKER_FILE}}
- Re-read the most recent round summaries/reviews that led to this round
- Write the current round contract to @{{ROUND_CONTRACT_FILE}}
- If this repo configures an auto-routing knowledge-base hook (e.g. a `UserPromptSubmit` hook that injects matching reference cards as system reminders), defer to those injected cards rather than re-opening primers preemptively. Otherwise, if a local primer exists at `.claude/knowledge/INDEX.md`, read it before coding or delegating domain-specific / modeling / metrics work. Skip this step entirely if neither is configured.

Your round contract must contain:
- Exactly one **mainline objective**
- The 1-2 target ACs for this round
- Which issues are truly **blocking** that mainline objective
- Which issues are **queued** and explicitly out of scope
- Concrete success criteria for this round

Do not start implementation until the round contract exists.

## Task Lane Rules

Use the Task system (TaskCreate, TaskUpdate, TaskList) with one required tag per task:
- `[mainline]` for plan-derived work that directly advances this round's objective
- `[blocking]` for issues that prevent the mainline objective from succeeding safely
- `[queued]` for non-blocking bugs, cleanup, or follow-up work

Rules:
- `[mainline]` work is the round's primary success condition
- `[blocking]` work is allowed only when it truly blocks the mainline objective
- `[queued]` work must be documented but must NOT replace the round objective
- If a new bug does not block the current objective, tag it `[queued]` and keep moving on mainline work

Before executing each task in this round:
1. Read @{{BITLESSON_FILE}}
2. Run `bitlesson-selector` for each task/sub-task
3. Follow selected lesson IDs (or `NONE`) during implementation

---
Below is Codex's review result:
<!-- CODEX's REVIEW RESULT START -->
{{REVIEW_CONTENT}}
<!-- CODEX's REVIEW RESULT  END  -->
---

## Goal Tracker Reference

Before starting work, **read** @{{GOAL_TRACKER_FILE}} to understand:
- The Ultimate Goal and Acceptance Criteria you're working toward
- Which tasks are Active, Completed, or Deferred
- Which side issues are blocking vs queued
- Any Plan Evolution that has occurred
- The latest side-issue state that needs attention

**IMPORTANT**: Keep the mutable section of `goal-tracker.md` up to date during the round.
Do NOT change the immutable section after Round 0.
If you cannot safely reconcile the tracker yourself, include an optional "Goal Tracker Update Request" section in your summary (see below).

## Mainline Guardrails

- Keep the mainline objective from @{{ROUND_CONTRACT_FILE}} stable for this round
- Do not let queued issues take over the round
- If Codex reported several findings, classify them into:
  - mainline gaps
  - blocking side issues
  - queued side issues
- Only mainline gaps and blocking side issues should drive the next code changes

## Objective Sidecar (REQUIRED when the verdict adapter is active)

If this repo has an active verdict adapter (signalled by `.humanize/adapter-config.json`, a non-empty `.claude/knowledge/problems/` directory, or a previous-round `round-<N>-objectives.json` already present in the loop dir), you MUST emit an objective sidecar before exiting this round:

- Write the sidecar to `.humanize/rlcr/<loop>/round-<N>-objectives.json` where `<N>` is the round number whose state-transition the verdict engine will gate next.
- Conform to the schema documented in `docs/solbench-verdict-engine-schema.md`: the 9-field identity block carries `sidecar_schema_version: "1.0"` plus `loop_id`, `round`, `adapter`, `objective_id`, `objective_hash`, `manifest_path`, `manifest_hash`, and `generated_at`. The remaining sidecar payload covers `correctness`, `latency`, `sol_score` nested provenance, `required_surfaces`, `rule_compliance`, and `rule_required_by_objective`.
- Compute `manifest_hash` as the SHA-256 of the manifest bytes; `objective_hash` as the SHA-256 of the canonical objective definition.
- Use the sentinel `"unknown_t_sol"` for the SOL score when stage-4 SOLAR data is not yet registered; never default to 0.0 or null.
- For each of the 9 CLAUDE rules report status `verified | violated | not_evaluated`. Mark `rule_required_by_objective.<rule_id> = true` only when the rule is materially load-bearing for the round's objective; otherwise leave it false.

If no adapter is active you may omit the sidecar; the verdict engine will record a `mode=skipped_no_adapter` row and the loop will continue normally.
