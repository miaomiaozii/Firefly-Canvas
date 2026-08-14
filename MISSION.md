# Mission: Build a Firefly Canvas quality system

## Why
Stop relying on the user to discover and report defects one at a time. Turn every important workflow and escaped bug into an automated signal so Firefly Canvas can evolve without repeatedly breaking model routing, media handling, canvas layout, or project state.

## Success looks like
- The user describes outcomes and priorities instead of manually hunting every defect.
- One repeatable quality gate catches high-risk regressions before a build is offered for trial.
- Every repaired bug leaves behind a regression test at the smallest correct seam.
- Provider protocols and model capability rules are explicit, versioned, and resistant to casual rewrites.
- Failures identify a narrow subsystem instead of requiring broad visual guesswork.

## Constraints
- Avoid paid provider calls in routine tests; use fake provider boundaries and captured contracts.
- Preserve original media fidelity and project data.
- Keep feedback loops fast enough to run during development.
- Teach in Chinese and ground lessons in the real Firefly Canvas codebase.
- Keep UI information visible and unobstructed. Prefer a complete single-row presentation when it fits, and adaptive layout when it does not.
- Treat generation, history, deletion, and recovery as one lifecycle: an action on one record must not silently remove or corrupt another task, card, or original.
- A repaired behavior remains part of the regression contract; a local fix is incomplete if it breaks an earlier verified behavior.
- Support desktop use only. The release matrix covers 1920×1080, 1440×900, 1280×720, 1024×768, and a minimum 800×600 window; phone layouts are out of scope.
- Let readable and editable text expand without losing content, and provide a text-only flow mode where sustained writing is part of the workflow.
- Let prompt-writing fields expand in one action to the natural height of their complete text while retaining an explicit way to extend the editing area further.

## Initial stabilization scope

The first quality gate covers the product path from the project list into a project's unique core canvas. The classic canvas receives only a legacy-access smoke test. Photoshop integration, video creation, and local editing are deferred until they enter the product path.

The initial release-critical journeys are:

1. Create, save, close, and reopen a project without losing canvas state or project-media identity.
2. Import, move, group, resize, and ungroup images without distortion or unexpected layout changes.
3. Select provider, model, aspect ratio, 1K/2K/4K, and ordered reference images; generate and persist the exact intended request and generation original.
4. Run prompt nodes with connected text and project media while preserving inputs, relationships, and results across save and reload.
5. Run overlapping generation tasks; observe pending, completed, and failed states; retry safely; and download the original result without degradation.
6. Complete the generation lifecycle from submission and live status through records and originals to deletion, undo, and recovery, without one task, record, card, node, or original silently damaging another.

Failure of any journey above blocks a trial build.

## Severity

- **S0 data catastrophe:** irreversible project-content or media-original loss, cross-project contamination or disclosure, or uncontrolled paid requests.
- **S1 critical blockage:** a release-critical journey cannot complete, a user parameter is silently changed, or the wrong model or original is used.
- **S2 material degradation:** behavior is wrong but has a reliable workaround and does not damage project content or originals.
- **S3 minor defect:** visual, wording, or low-frequency interaction defects that do not alter task results.

Severity and frequency are recorded separately. Known S0 and S1 failures block both trial and release. S2 blocks release by default but may enter a trial with explicit user acceptance. S3 may be recorded and released.

Frequency uses:

- **F4 deterministic:** occurs in every normal attempt.
- **F3 frequent:** occurs in at least one of three attempts.
- **F2 intermittent:** below one in three but capturable by a repeatable loop.
- **F1 rare:** observed only in the field or a special timing window without a stable reproduction yet.
- **FU unknown:** evidence is insufficient; unknown is never treated as rare.

Priority records both severity and frequency. S0 and S1 never become releasable merely because they are infrequent.

## Gates

- The **trial gate** runs before a build is offered to the user for hands-on evaluation.
- The **release gate** runs before packaging or external release and includes the slower full regression suite.
- Routine gates use parameter matrices, captured contracts, fake HTTP, and fake-provider browser tests.
- Real-provider certification is manual and explicitly authorized; it is never part of routine regression and must not be inferred from a fake-provider pass.
- Test processes deny non-localhost network and real-provider access by default; exceptions require explicit authorization.
- The fast gate targets 30 seconds, the domain gate 2 minutes, the trial gate 5 minutes, and the release gate 15 minutes. These budgets drive optimization and never turn a timeout or failure into a pass.
- Local development and future CI use the same quality-gate entry point with `fast`, `domain`, `trial`, and `release` modes. A dirty working tree may be tested; the report records its state rather than requiring a commit.
- A trial or release evidence bundle records journey steps, native click positions, fixed-environment screenshots, failure screenshots, console errors, request-contract summaries, exact commands, selected tests, durations, and verdicts without secrets.
- The user accepts the initial visual baseline. Intentional visual changes update it with an explicit reason; failures never overwrite the accepted baseline silently.
- Windows stable Chrome is the primary browser gate. Stable Edge runs in the release compatibility gate. Both use fixed DPR, fonts, light theme, and the five supported desktop window sizes; Firefox, Safari, phone, and touch are out of scope.
- A retry may collect evidence but cannot erase the first failure. Any flaky release-critical journey blocks until its cause is removed or a deterministic replacement covers the behavior; unlimited green-seeking retries are forbidden.
- The quality gate enforces the retry rule strictly with zero evidence retries (`allowed_evidence_retries = 0`, `first_failure_preserved = true`): any retry evidence fails its owning step and never converts a failure into success, so gate evidence must be a clean single-pass run. A retry may still collect diagnostic evidence outside the gate.

## Interface contract

- Model, aspect ratio, resolution, task state, error reason, destructive-action meaning, and current selection remain directly visible or expand through one explicit click or touch action.
- Information required for a decision never depends on hover alone.
- Non-decision text such as long project names and prompts may be visually truncated only when the complete value remains explicitly expandable or focusable.
- Icon-only tools retain stable tooltips and accessible names.
- Supported desktop sizes keep critical controls reachable, avoid critical overlap, and avoid page-level horizontal overflow.
- UI evidence must include screenshot inspection and actual click-driven user journeys, not only static source or DOM contract checks. Geometry assertions support that evidence but do not replace it.
- Critical journeys use browser-native coordinate clicks, keyboard input, and drag operations rather than relying only on synthetic DOM events. Screenshots use fixed theme, DPR, fonts, and desktop viewports and are retained when a check fails.
- Compact controls generally use 6–10px corner radii and inner spacing. Ordinary border strokes remain thin; nested thick frames and decorative empty shells must not displace useful information.
- The initial flow-mode contract covers the generation Composer and the prompt node's main body. Connected prompt previews, generation-history prompts, and generation errors expand for complete reading. Conversation, Agent, template, Loop, and local-edit text adopt the same contract later without blocking initial stabilization.
- One-action prompt expansion grows to the text's natural height while primary actions remain reachable. Content taller than the window moves into a scrollable flow layer; the user may extend the editing area further and can restore the previous manual height.
- Flow-mode text remains part of the current project draft and normal autosave. Exiting preserves text, selection, IME state, undo history, and source relationships; `Escape` exits without discarding, generating, or sending. Generation occurs only through an explicit control or an already-established explicit shortcut.

## Maintainability contract

- Comments explain invariants, races, compatibility reasons, external side effects, paid boundaries, and non-obvious escaped-bug causes; comments are not added at arbitrary line intervals.
- Navigation favors a maintained domain-to-symbol-to-test-to-ADR index and stable domain regions over reading large files from beginning to end.
- Production modules may be split incrementally when a real domain boundary and verification seam justify it. Quality-governance work does not use unrelated refactoring as a prerequisite.
- Local structured logs correlate request, project, task, provider, model, state, and failure events without recording API keys, complete prompts, media contents, or complete local paths.
- Each provider/model-family contract records its parameter matrix, reference order, final payload, response shape, primary documentation or reference-implementation URL, retrieval date, and local fixture. Contract changes require explicit review; routine validation remains offline.
- Accepted ADRs, project-data formats, public interfaces, release-critical journeys, regression tests, and confirmed domain invariants cannot change silently. Intentional changes require an explicit issue or specification decision and corresponding contract updates.
- Every escaped defect gains a deterministic red-capable reproduction before a production fix and leaves a regression test in the routine gate afterward.
- Large modules are split in a separate, behavior-preserving ticket when a change crosses domains, lacks a minimal verification seam, repeatedly regresses in the same area, or imposes persistent navigation cost. Refactoring is not hidden inside an ordinary bug fix.
- Local structured logs rotate after 7 days or 100 MB, can be cleared by the user, and are never uploaded automatically. Any future diagnostic export is explicit and redacted.
- User-visible fixes do not ship before their red-capable reproduction and regression test. Documentation and isolated test-infrastructure changes may omit browser suites they cannot affect; S0 and S1 never bypass their gates.

## Destructive operations

- Deleting a generation record removes only that record.
- Deleting a generation original removes that original and its direct project references according to the accepted project-wide deletion decision, without deleting completed downstream results.
- Cancelling a generation task stops only that task.
- An original used as input by a running task cannot be deleted until the dependent task is cancelled. Deleting an older original from a result node does not cancel a newer task running for that node.
- Project cleanup is a separate operation launched from the project list only while the project is closed and has no generation, import, Agent, conversation, Photoshop, export, or other active session. It permanently removes only media with explicit failed/transient provenance that is proven to have no project-content reference, generation record, recovery owner, or pending handoff.
- Imported media, media intentionally saved to the project library, unknown or damaged ownership data, and unparseable projects are never cleanup candidates. Cleanup shows candidate count and thumbnails before confirmation; after confirmation it has no backup, undo, or recovery path.

## Out of scope
- Trying to prove that the product has zero defects.
- Replacing all exploratory human judgment with automation.
- Adding new product features during the initial stabilization pass.
