# #493 image submission policy — reconciled integrated contract

Current source includes the parent-integrated `b738` retry reconciliation. Authority is the actual `MESSAGES.md:3813–3816/3847/3852–3860/3865–3866/3871` under `D:/Firefly-Canvas/.local/agent-work/channel/review-batch-ui/`; review `01a1ef85` was explicitly confirmed by user `m05149`. This is not whole-ticket acceptance.

This supplements `image-generation-integration-493.md`; it does not certify the whole ticket.

## Changed boundaries

- `app/core.py` recognizes the existing `image_generation` tool or tool choice in a Responses body. Image background and direct SSE helpers never fall back to another POST after a status or transport error. Accepted background IDs continue through GET only. Text-only Responses retains background → SSE → direct compatibility, including its previous transport-error fallback. No body, header or parameter representation is changed.
- `app/services/generation.py` explicitly opts the native Gemini, generic OpenAI/Responses/fal, ModelScope, RunningHub (both dedicated routes), and Volcengine clients into the SDK's maximum two additional upload attempts. Only installed transport evidence plus replayability can authorize these attempts; backoff is 1s/3s. A task-inherited allowance is shared per logical image across legacy/Agent business attempts and child candidate tasks; neither the outer semaphore nor an SDK-finalized failure restarts it. Existing custom factories still receive only their original constructor arguments.
- Necessary additional scope: `app/services/image_transport.py` no longer treats an unobserved body-yield count as zero when evaluating an incomplete upload. Header-write failure without a body count is conservatively non-retryable. Connect failure still requires positive SDK trace evidence. Full-yield/full-write failures, missing trace, unknown exceptions, cancellation, statuses and accepted tasks never authorize SDK upload replay. Separately, a positively classified definite rejection may use the existing business policy: legacy bounded 429 backoff/Retry-After is preserved. Recursive task IDs, results/URLs or acceptance states override rejection; ambiguous 408/5xx/read/write outcomes never authorize regeneration. Agent-approved business retries remain Agent-owned; this is not proof that any arbitrary 429 is uncharged.
- MiniMax retains its stricter existing zero-retry contract. Image video-proxy synchronous multipart stays one POST; no sync SDK retry support is claimed. Download retry/recovery remains D2-owned; no download, project persistence, frontend, proxy/TLS/SSRF policy or provider flag schema was changed.

## Review limits

Dedicated HTTP submission paths in generation.py were inspected: ModelScope, RunningHub entry/model, Gemini, Volcengine, MiniMax, generic OpenAI/Responses/APIMart/fal and image video-proxy. RunningHub polling POSTs query an existing ID; they are not generation submissions. No submit fallback was found in these HTTP-only dedicated paths after the Responses fix.

Native/CLI behavior intentionally remains unchanged: Codex skill has an authentication-provider fallback; local ComfyUI has a post-acceptance OOM resubmission. These are not certified by this work and prevent a universal all-adapter no-resubmission claim. Native host/CLI internals, real TLS/proxies, live providers and paid calls were not exercised.

## Final retry evidence

`C:/ff-qa/493-retry-final-61dae08980b2481a84fa9d0ed8ee3a38/REPORT.md` freezes four source files (`generation.py`, `generation_failures.py`, `image_transport.py`, and the necessary per-item scope in `agent_batch.py`) and **31 new tests**, integrated by parent as `b738` (`tests/test_retry_final_493.py`). The external pristine run reproduced nine original failures; its final candidate recorded **242 passed, 1 failed, 16 subtests passed, zero skipped/deselected**. Eight retry regressions passed unchanged; the remaining Gemini case patched the legacy helper instead of the generation helper and failed before any provider POST. The optional fixture-binding experiment is not the unchanged-fixture result. These are selected offline results, not a green final full gate; current gate limitations are in `image-generation-integration-493.md`.

The mixed partial → definite 429 → partial → definite 429 → partial case uses five wire attempts but only two upload retries total, including real Agent task inheritance. Accepted/ambiguous/read/no-evidence cases do not regenerate; MiniMax and native/CLI exceptions remain as above.

## Historical F evidence (superseded source, raw logs retained)

`C:/ff-qa/493-F-7cb62077-e25d-4beb-a5b0-74cf8ddce349/` owns sixteen isolated environment roots, credential-free env-i runner, pre-edit source copies/SHA256, logs and final source copies/hashes. Tests block sockets/DNS except Windows stdlib self-pipe; they execute real function ASTs and installed HTTPX 0.28.1/httpcore 1.0.9 with an in-memory network backend, not MockTransport-only proof. No app bootstrap or native/browser/paid/full/fast/Git action occurred.

Baseline core regressions: `before.log` has 6 failures/10 passes proving old image fallback attempts; original failures are retained. `after.log` records two new-harness missing-dependency errors, corrected by supplying explicit image parameters, not by changing production behavior. `final.log`: **128 passed, 1 deselected** across the new policy tests and existing SDK/pipeline tests. Coverage includes image 400/429/5xx, full-write/read and ambiguous write errors, unknown/cancel, accepted-ID GET-only polling, bounded native and actual generic/Responses budgets, dedicated HTTP adapters, unchanged text fallback and provider concurrency.

The F run deselected `test_actual_installed_native_outer_budget_is_one[incomplete]` because it asserted the former zero factory budget. That **128 passed, 1 deselected** result is historical, not the current handoff or an outstanding instruction to reconcile the final retry patch. The later final retry cohort above executes SDK/pipeline/policy and original concurrency cases without skip/deselection; raw F logs and original RED evidence remain untouched.

The F worker had not located the original channel messages at that time. This documentation reconciliation has now read the authoritative messages at the path above; that historical discovery limitation is not a current authorization blocker.
