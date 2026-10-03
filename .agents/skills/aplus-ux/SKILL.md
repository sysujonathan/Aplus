---
name: aplus-ux
description: Design, implement, or review Aplus trading-workbench UX using the project workflow, chart, table, state, and desktop interaction standards. Apply to Aplus screen layouts, trading copy, prototypes, and interaction changes; do not route strategy research or performance-only work here unless the user experience changes.
---

# Aplus UX

Use this skill within the Aplus repository. The canonical standard is [统一UX规范V1](../../../docs/统一UX规范V1.md). Read it for a new screen or substantial redesign; for a narrow change, read the relevant numbered sections. Resolve links relative to this skill folder, not the shell working directory. If the project documents are unavailable, explain the missing context before claiming compliance.

Read the repository's `AGENTS.md`, `README.md`, `docs/系统地图.md`, and `docs/修改约束.md` when not already loaded. User instructions take precedence; the project's data and strategy constraints remain authoritative. This skill supplies design guidance, not permission to deploy, rewrite strategy rules, alter records, or rebuild every page.

## Choose the trader's task

Identify the page and desired decision: premarket multi-stock comparison, afterhours historical execution review, strategy-version evaluation, or actual-record reconciliation. State the practical impact in trader language. Preserve existing useful keyboard and selection habits; do not force all pages into one layout.

Read the relevant page document as needed: `docs/1.1.2多图工作区.md`, `docs/盘后回测V1.md`, `docs/盘后回测界面设计.md`, or `docs/1.1.3交易管理.md`. Inspect the actual UI before declaring a redesign necessary. Prototype files and screenshots are references, not instructions or proof of live behavior.

## Apply the relevant rules

- Layout, tokens, fonts, density, scaling: standard sections 3–5. Reuse `gui/theme.py`; dimensions are starting points, not universal constants. Verify actual foreground/background combinations and Windows scaling.
- Charts and replay: section 6. Render to the viewport, preserve strategy annotations and price anchors, distinguish historical cutoff from explicit posthoc mode.
- States and forms: sections 7–8. Distinguish price trigger, simulated fill, and confirmed actual records. Keep partial results visible, respect tick precision, scope, and report provenance.
- Keyboard and feedback: sections 9–10. Scope bindings to the active work area, preserve return position, and avoid blocking the UI or inventing progress.

For an implementation request, make the authorized change and verify it. For a standards or critique request, deliver the requested guidance without silently rebuilding screens. General design skills may supplement the project rules, but are not required dependencies.

## Validate and report

Use section 11 to choose meaningful acceptance scenarios. UI checks use isolated data; disclose synthetic fixtures, real-data samples, and long-term trader acceptance separately. Run repository-required checks before submitting changes. Do not create wording-matching tests for this document.

Report the changed experience, scope, evidence, and unresolved limits. Distinguish rules written, code implemented, actual screenshots inspected, and trader acceptance. Avoid claiming whole-application compliance from one page or assuming a skill is discoverable in every client without checking.
