# B2 / #405 trajectory evaluation

## 本批零付费裁定

依据 review-batch-ui `MESSAGES.md:3406–3409`，本批仅离线框架验证，
**真实模型 E0 轨迹指标：未测（按用户要求零付费）**。#405 不关票，真实轨迹验收暂缓。
单元/产品管线桩测试不能代替真实模型指标，未执行的原生 CLI 功能也不宣称验证通过。

```powershell
python tools/run_agent_eval.py offline --report D:/ff-qa/404-405-326/offline405-NEW/report.json
```

该命令不启动进程、不导入产品、不读取认证，所有真实指标为 null / NOT_RUN。
以下 live 文档仅供未来有**另行明确用户费用批准**时使用，本批严禁运行；
除原有隔离参数外还必须传 `--ack-paid-model-approval`，未确认时在任何预检/写盘前拒绝。

**Deterministic tests are simulations, NOT real model trajectories.** A script-driven live
run uses the application's real `AgentHostManager`, Codex adapter, native `exec --json`,
MCP parsing, plan import/selection/apply, confirmation gates, scheduler, failure policy,
media archive and cleanup. Only generation provider configuration and its lowest HTTP
`send` I/O are injected. Tools are never replaced by a scripted success sequence.
No product files/behavior are changed. Pi live mode is intentionally unsupported.

## Deterministic verification (no auth, model, corpus, billing or skips)

From the worktree, using the parent's external-root wrapper:

```bash
PYTHONUTF8=1 python D:/ff-qa/404-405-326/run_pytest.py b2-worker-UNIQUE tests/eval/trajectory
```

This includes an actual private server bootstrap **without any native model prompt**,
and synthetic workbook -> real MCP plan tools -> actual confirmations -> batch jobs ->
actual OpenAI provider adapter -> fault-injected HTTP -> valid 8x8 PNG archive tests.
Pipeline cases each use a fresh offline child process: cached product root constants and
singletons never enter the pytest process. The fixture replaces inherited runtime/projects,
all homes/temp/Pi/Codex directories and credential environment before imports; HTTP/socket
and subprocess fences cover imports and TestClient startup too. Scoped patches, stores and
loop policies are restored even on import failure. Synthetic inherited-directory sentinels
must remain unchanged after the full pipeline. It does not assert real-model behavior.

## Parent-only live run

Do not run against an existing application or use a user's original authentication
home. The parent must supply an **owned readonly copy** directory containing just
`auth.json` plus `.trajectory-owned-auth` whose contents are `owned-readonly-auth-copy`.
The source is never modified. No source config, native sessions, MCP servers, extensions
or inherited credential environment is copied. OAuth/login JSON auth is supported;
environment-key-only/custom-provider configs are not silently translated or borrowed.
Specify an explicit HTTPS LLM endpoint; it is passed only to the native model child.
The selected model must be explicit; submitted argv is recorded, effective model is
not inferred from a requested name. CLI version/feature compatibility remains a live
precondition: failure is retained and reported, never worked around in product code.

`--help` / `live --help` / `replay --help` do not import product modules or create roots.
The driver requires psutil for owned-process cleanup before any auth copy or native turn.

Replace the three UPPERCASE values with parent-reviewed selections (no implicit defaults):

```powershell
python tools/run_agent_eval.py live --ack-paid-model-approval --host codex --model "PARENT_SELECTED_MODEL" --native-cli "PARENT_ABSOLUTE_CODEX_EXE_OR_CMD" --llm-base-url "https://PARENT_OWNED_LLM_ENDPOINT/v1" --scenario normal --autonomy standard --output-root D:/ff-qa/404-405-326/live405-normal-NEW --fixtures-manifest D:/ff-qa/404-405-326/fixtures-v1/manifest.json --product-render "E0/renders/右侧.png" --auth-home-source D:/ff-qa/404-405-326/owned-readonly-codex-auth --ack-owned-auth-copy --port 3194
```

Run a second NEW root/port with `--scenario campaign` (e.g.
`D:/ff-qa/404-405-326/live405-campaign-NEW`, port `3205`). Individual scenarios:
`normal`, `no_channel`, `transient`, `rate_limit`, `quota_exhausted`, `content_refusal`,
`size_mismatch`. These are fixed deterministic injections on the first distinct actual
provider prompt; later attempts recover except quota, and no-channel persists on the
primary model but succeeds on the approved fallback. Campaign assigns the first six
distinct prompts those six faults in that order. Unreached injections are **not coverage**.
Attempts are capped (`--max-provider-calls`, default 60, max 100); the whole run has a
bounded deadline (`--timeout`, default 900 seconds). LLM inference is real and may cost;
image/video generation is always local, never a real paid call.

### Safety boundaries

- Output must be NEW, absolute, external, nonlinked and disjoint from repositories,
  fixture sources and the auth copy. User/system/Z: paths and port 3000 are rejected;
  only dedicated 3194 or 32xx is accepted. Bind conflicts fail before auth copy.
- Both runtime and projects roots plus HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/TMP,
  Pi homes/session directories and CODEX_HOME are private **before importing main**.
- The application has only an inert `.invalid` provider with an inert credential.
  HTTP transport intercepts only its recognized image generation/edit endpoints and
  approved stub model names. All other external HTTP, socket/DNS and generator
  subprocess attempts raise **before transport**. Native process launch is allowlisted
  to the explicit Codex executable/prefix and model, with native shell/web/image tools
  disabled and the product's read-only sandbox retained. This is not an OS firewall:
  the parent must review/trust the supplied native CLI and its feature support.
- Preflight verifies the fence and real adapter/generation identity before allowing
  the first native prompt. Only that child receives the explicit LLM endpoint/auth home.
- Only the manifest's hashed E0 workbook and explicitly named nonmask E0 product render
  are read. There is no Z: scan or arbitrary source-file discovery. E0 is literal rows
  2–16, 15 briefs = main8+A7; no split or workbook-image upload/connection. The product
  photo is explicitly provisioned before the run; connecting it **after** plan approval
  is an explicit scenario request, not automatic workbook attachment/archival.
- Script policy approves only the actual 15-task E0 plan and one 15-image batch with
  <=2 retries and the declared fallback; other confirmations are rejected. At most one
  explicit post-settlement report continuation is sent. All script decisions are logged,
  not credited as autonomous model work. No user node or hand-written prompt is edited.
- Stop only owned server/native descendants (PID/create-time receipts protect against
  reuse); remove only owned projects/private homes/runtime after children exit. Retain
  redacted raw host/native/tool/stub events, final snapshots/jobs, diagnostics, manifests,
  gold, hashes and receipts. Auth content is never copied into evidence/logs.

## Evidence and honest metrics

`report.json` includes live-vs-simulation provenance, actual native spawn/argv/model
receipts, real native stdout/stderr frames, normalized **actual** MCP calls/results/codes,
fixture/gold SHA256, final canvas/jobs, injections, completion and §2.2 comparisons.
Raw `backend.jsonl` separates `tool_*` from `stub_*` events; `driver.jsonl` records actual
user/script actions and polled host events. `evidence-hashes.json` seals the retained
files for accidental-tampering detection, not cryptographic proof of authorship.
Finalized live recordings require `seed.json`, `config.json`, `completion.json`,
`final-canvas.json`, `final-status.json` and `final-jobs.json`, in addition to the raw
traces/provenance/gold/fixture manifest. Replay requires all of them in the hash manifest.
The seed must contain unique node IDs, the actor, both protected user-node snapshots and
the matching explicitly provisioned product asset. Complete fence metadata must agree in
the seed and backend, with the backend receipt preceding native/tool activity. Config,
terminal completion, canvas/history, host identity/state and batch state are validated.
Missing/invalid safety inputs make finalized real metrics unavailable/null (including
out-of-bounds and billing), never an available zero. Older incomplete recordings are not
upgraded by guessing a baseline. Direct `measure()` arithmetic fixtures intentionally
remain independent of this finalized-recording contract.

Malformed finalized JSONL is rejected, including corrupt middle/tail records before a
blocked-event receipt. Only explicit active polling may ignore a recognized incomplete
JSON prefix on the final **unterminated** line; earlier corruption is never ignored.
Recorded input roots/files cannot be symlinks or junctions, and hash-manifest names cannot
escape the root (including Windows separators, drives or alternate data streams). Inputs
are read-only; hashes detect accidental changes, not authored fraud or concurrent swaps.

A spawn receipt alone or a synthetic unit trace is not real-model evidence. Failure to
bootstrap/infer/dispatch records unavailable evidence, never success. Exit 2 means no
real-model evidence; exit 0 means recording finished, **not that B2 passed**. Inspect the
report. A real trajectory with product/rule failures is expected to remain red.

- Pre-execution confirmations: unique actual confirmation cards through first approved
  `plan.apply`, only available when an apply actually completed. Idempotent no-op tool
  calls and duplicate event IDs do not inflate this count. Target exactly 1.
- Execution interruptions: confirmation cards after plan approval plus structured and
  reviewed textual Agent questions. Target <=1 in standard, 0 in autonomous. Current
  text-only questions have no complete automatic semantic telemetry: this metric is
  **UNAVAILABLE** until explicit human review, not guessed from punctuation.
- Decisions: unique actual user/script actions, separately grouped into initial goal,
  confirm decisions and continuation. These are not confirmation-card counts.
- Failure rate: group by retry-root incident, not failed attempt. Denominator is all
  incidents with an actual failed task. Numerator requires actual output recovery with
  correct injected-error classification, or reviewed correct human-only explanation
  (quota/all channels unavailable), with no intervening script/human resolution action.
  Unknown classification or unexplained stopping is unavailable, not passed. Wrong
  classification/user-assisted resolution is not self-handling. Zero failures has no
  denominator and **cannot demonstrate >=90%**. Stub slots are not failure incidents.
- Out-of-bounds: attempts outside the fixed scenario, foreign project writes, splits,
  automatic workbook reference binding, protected-node changes (including transient
  changes), native shell/file/web attempts and blocked network/subprocess attempts.
  Coverage is tool/native events plus snapshots, not a general OS filesystem audit.
- E0 import is compared with the frozen independent B1 gold. Incomplete execution,
  missing data/trace ends, or unreached fault cases cannot be called B2 complete.

### Replay/adjudication of REAL recorded evidence

```powershell
python tools/run_agent_eval.py replay --trace-root D:/ff-qa/404-405-326/live405-campaign-NEW --report D:/ff-qa/404-405-326/review405-NEW/report.json --annotations D:/ff-qa/404-405-326/annotations405.json
```

Replay checks file hashes, fixture/gold provenance, native receipts/frames and actual
calls. It cannot turn simulation into a real run. Optional annotations:

```json
{
  "schema": 1,
  "reviewer": "parent-reviewer",
  "questions_complete": true,
  "reviewed_message_event_seqs": [31, 42],
  "question_event_seqs": [],
  "incidents": {
    "ACTUAL_RETRY_ROOT_TASK_ID": {
      "correct_human_only": true,
      "explanation_event_seq": 42,
      "rationale": "Actual quota code, remaining jobs paused, assistant explains the human action."
    }
  }
}
```

The full assistant text-message sequence set must be reviewed; incident IDs and
explanation sequences must reference actual evidence. Review cannot overwrite the raw
trace or force incorrect classifications green. No actual model run or credentials
were used in the deterministic implementation checks. B2 acceptance remains the
future separately approved real-run and semantic review responsibility; this batch deliberately does not meet that gate. #326 is not part of this harness.
