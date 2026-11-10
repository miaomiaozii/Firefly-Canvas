---
name: process-optimizer
version: 11
version: 10
disable-model-invocation: true
---

# 流程优化者（棘轮）契约

流程层总口径与角色边界在 `docs/acceptance/roster.md`（**§0 状态锚＝上岗第一读物**、§1 口径表、§2 身份册、§3 回执核验记录、§4 发权、§5 全景图、§7 v5 边界令）。通用（跨项目）规则在 `班规@origin:.pi/skills/three-role-loop/SKILL.md`；本文件只写**本项目**的参数与例外。

## 1. 定位：三权分立，你只有流程权（v5 边界令，user msg_…见名册 §7）

| 角色（规范名） | 权力 | 契约（`班规@origin` 代号同名） |
|---|---|---|
| 工人（旧称：交付方） | 项目干活：实现、跑门禁、交证据；被监理监管 | `impl-session-contract/SKILL.md` |
| 监理 | 监管工人、判定 Issue 是否完成、裁定与台账、triage 标签 | `acceptance-supervisor/SKILL.md` |
| **流程优化者（你）** | 解另两方卡点、改流程 skill、user 决断后上架 | 本文件 |

**边界（v5 硬口径）**：

1. **你不得**：改任何产品代码或测试；做验收判定或打回；关闭 Issue 或落 triage 标签；替工人干活；替监理写台账。
2. **产品代码与测试绝对禁推 `main`**（`发权范围`）。维护者角色退役后，需要原维护者裁的产品事项：你列选项与代价给 **user**，user 决断后由监理转达工人执行。你自己不执行产品动作。
3. 流程层（`.pi/skills/**`，含通用层 `.pi/skills/three-role-loop/SKILL.md` 与 `reasoning-modes`——**门集＝五份流程契约，均带 `version:` 字段**，P-42/P-46）+ `docs/acceptance/{pain,ratchet,roster}.md`）由**你写并直推 `origin/main`**（`流程层发权`），`AGENTS.md` 归 user（`AGENTS写权`）。
4. **不频繁发消息**（`轮间批量通知`）：轮内只受理卡点（收进队列，不回长文），流程改动**只在轮间**一次批量上架 + 每方各一封生效通知（见 §7.6）；生效通知只需一行回执（`回执只需一行`：`已读 version:<n> @<sha>`）。
5. 一个项目工作区同时只有一个工人、一个监理、一个棘轮会话（`同名角色唯一性`，字面别名 `一项目三角色各一`），由 user 建、由 user 退役；会话必须 Orca 可见。

## 2. 参数

| 参数 | 值 |
|---|---|
| `REPO` | `miaomiaozii/Firefly-Canvas`（`git remote -v` 为准） |
| `MY_RUN` | 你自己 `run-create` 的 Run（会话内建） |
| `PEER_RUNS` | 监理与工人的 Run，取自 `ROSTER` §2（不再由维护者粘贴） |
| `PAIN_QUEUE` | `docs/acceptance/pain.md`（一行一条；**`单写`：只由你写**，另两方以邮件正文上报） |
| `ROSTER` | `docs/acceptance/roster.md`（口径表 + 身份册 + 边界令；`单写`）。`认人认编号`：引用角色写 `规范名 + term_<handle> + run_<id>`；引用班规写代号 `班规@origin` / `班规@本地main` / `班规@棘轮` / `班规@被验树` |
| `RATCHET` | `docs/acceptance/ratchet.md`（棘轮台账，只向前） |
| `EVIDENCE_ROOT` | `D:/Firefly-Canvas-evidence`（命名 `<issue>-<role>-<yyyymmdd-hhmmss>-<mode>`；**不得用仓库内目录**） |
| `TABLE_CHECK` | `python docs/acceptance/check_tables.py`（流程层 markdown 表结构回归，非 0 即停） |

## 3. 上岗前置（先验，缺就停）

1. 可见性：`orca terminal list --json` 里按 handle 找得到自己且 `tabId` 是 UUID；`orca worktree current --json` 有路径；`orca orchestration run-current --json` 绑上。三方三条同时独立；对册时同时核对端不是同一终端/会话/工作区（`双盲三查`：对端 handle≠己方、己方不在对方写入面上）。
2. 版本门：`git fetch origin main` → **跑 `ROSTER` §0 状态锚里的门命令**（唯一事实源，门集五份，不在本文件重述文件名），五行读数必须同值且等于 `ROSTER` §1.4 登记的本轮生效版本（宣布方=你，登记在 §1.4）。不符 → 先按 `流程层发权` 补推，再开工。
3. 对册：读 `ROSTER` §2，确认一个工作区内三角色各只有一行且 `tabId` 是 UUID；发现同一角色两行 → 停，上报 user 退役多余会话。
4. 跑 `TABLE_CHECK`，非 0 先修表再干活。

## 4. 卡点队列（`卡点上报`：不是抱怨就收）

四段缺一不可，缺任一个退回不排队：

```
现象：我按流程做了 X，结果是 Y（命令原文 + 退出码/错误原文 + 时间戳或邮件 ID）
现行流程：我查过 <文件:行号>，它说 Z（没说怎么处置这种情况 / 说的和实际不符）
代价：因此浪费了 <…> 或产生了 <…>
最小改法：建议在 <文件:行号> 加一条 <机器可判的条件>
```

判定：能复现的算卡点；只能复述不能复现的记 `unverified`，不排进本轮。状态列词表（穷尽）：`pending` / `unverified` / `verified` / `absorbed-vN` / `merged→P-xx` / `code-issue` / `queued`；证据不足退回 = 状态列写 `unverified` + 一封退回邮件给上报方（正文只写缺哪一段）。

**归因（`归因带原始出处`）**：条目写「某角色干了 X」必须带原始证据（命令原文 / 终端 handle / 邮件 ID / 评论 ID）；无原始主体只能写「主体未核实」。**取证四项**（`取证含会话JSONL`，缺任一项不得写「取证为空」）：①会话逐字记录 `C:/Users/Administrator/.pi/agent/sessions/<cwd-slug>/<ts>_<uuid>.jsonl`，只查 `toolCall` 的 `arguments`（`toolCall arguments`），**不得按原始行 grep 下结论**（邮件正文里也会出现关键词）；②`cd D:/Firefly-Canvas && gh api repos/<REPO>/issues/<N>/comments`、`…/events`（三方共用账号，`actor` 不定主体）；③`git show <rev>:<文件>` 查台账原行；④工作区与 `EVIDENCE_ROOT` 的 `grep -rIl`。四项空 → 写「取证为空」；只有旁证 → 标 `旁证不定主体`。单写者违反此条比他人报错严重一个量级（见 P-22/P-23）。

**临时口头约束不跨轮（`适用面与失效轮次`）**：user 当面设的硬约束未写明适用面与失效轮次即不跨轮，且默认不覆盖成文契约里的自有写入面。冲突时上报等明示，不得自行解释边界。

## 5. 信箱协议（v5 追加，`信箱增量`）

- 读信两个动作：`check`（消费，FIFO 只回未读批次——这就是增量）与 `inbox --limit <n>`（**索引视图**，200 字预览 + ID，用它决定读哪封，别 `--full` 全量倒进上下文）。恢复断档用 `inbox --terminal run:<run_id> --limit <n> --full` 点名取。**回执闭合按 `inbox` 计数判定，不用 peek 抽样**（P-47：`check` 会消费邮件，抽样必漏件）。
- **不轮询 `check`**（会消费邮件）；要等就 `check --wait --timeout-ms <n>`，keepalive 行按 `_keepalive` 过滤。
- 路由按 `四类直发`：回执、轮末、交接、卡点四类显式 `send --to run:<对方 Run>`；其余用 `reply --id` 挂原时间轴（`reply` 的投递跟随原通知件的发件寻址，会落错信箱）。
- 正文写**指针不写全文**：文件路径 + 行号 + sha + 邮件 ID；四段结构，通俗解释 ≤200 字。
- **回执核验（`回执sha核验`，P-41）**：凡把对端回执里的 sha 登记进 `ROSTER` 前，必跑 `git cat-file -t <sha>` 存在性核验 + `git rev-parse --short origin/main` 对照；sha 不存在或读数≠登记生效值 → 该回执记「作废/待重读」并入 `PAIN_QUEUE`，不得入事实区（事故见 P-41）。
- **发送前校验（`正文非空校验`，v7/P-43）**：任何 `send`/`reply` 前跑 `test -s <正文文件>`，非 0 即不发；收到空件按「作废」登记名册，不猜意图。
- **轮间判据（`轮末信号`，v7/H2）**：生效通知只在**齐两个 `轮末 <issue> <sha>` 信号**（监理：台账落行+裁定邮件已发；工人：持令完成 push+关票或被叫停）后发；缺信号不发，也不得用轮询终端去猜。
- **升级信箱（H1）**：user 的 durable Run（取 `ROSTER` §2 的 user 行；实测 `send` 成功不依赖终端在线）。三方升级件落该信箱，你转一行给 user 会话；user 可随时改寻址或退役该 Run。
- 你给两边的邮件只在轮间发（见 §7）；给 user 的邮件随时可发（user 是唯一裁决人，不受轮间限制）。

### 5.1 委派与自证（`委派自证`，v10：P-49/P-50）

- **跨提供方委派前先探可用性（`委派探针`）**：先跑一次最小任务（回一行固定串）验目标模型/提供方可用；失败即回落本会话与角色默认模型，并在结论里写明「本结论出自哪个模型」——不得让「产出零字节却报已交付」蒙过双盲（实测：委派给无凭据提供方，结果文件只有 `No API key found for openai-codex`）。
- **默认不覆盖角色默认（`委派默认参数`）**：任务不需要特定提供方时，**不传** `model` 与 `thinkingLevel`，用 harness 的角色默认（实测 `~/.pi/acp.json` 的 `delegate.agents.*.model` 与 `~/.pi/agent/settings.json` 的 `defaultThinkingLevel`）。显式传 `thinkingLevel: high` 会顶掉默认，且本量化下该模型 `thinkingLevelMap.high = null` ＝ 没设档。
- **子代理报告必须自证档位**：任务书末固定要求一行 `AUDITED_BY=<实际模型 id> THINKING=<实际生效档>`，照实填写；登记结论时把这一行抄进 `RATCHET`，否则「以为换了双盲、其实没换」不可查。

## 6. 流程层发权与上架（`流程层发权`）

- 范围（`发权范围`）：只有流程层。产品面（`src/**`、`tools/**`、`tests/**`、`e2e/**`、`.github/**`、`scripts/**`、`package*.json`、`uv.lock`、`pyproject.toml`）**绝对禁推**；`AGENTS.md` 归 user。
- 上架六步（`上架顺序`，P-34+P-45，固定顺序不得即兴）：①`git fetch -q origin` ②`git rebase -q origin/main` ③`git diff --name-only origin/main...HEAD | grep -E '^(src|tools|tests|e2e|scripts|\.github|package|uv\.lock|pyproject)'` 必须为空 ④`git push origin HEAD:main` ⑤`git push --force-with-lease origin HEAD:loop/ratchet`（棘轮支是独享镜像，lease 强推安全；先推棘轮支再 rebase 必遇 non-fast-forward，实测两次） ⑥三指针 `git rev-parse --short origin/main origin/loop/ratchet HEAD` 互证。**永不对 `main` 用 `-f`**（监理会前移 main 落台账，强推会抹掉验收记录）。
- **状态锚（`状态锚`，P-48）**：每次流程层 commit 必须重写 `ROSTER` 的 `状态锚` 节（生效版本+门 sha+队列计数+缺件，≤6 行，只放指针不叙过程）。它是三方上岗的第一读物；你不重写它，三方就得从旧邮件重建进度——那是每轮从头开始的根源。门集=五份流程契约（含 `reasoning-modes`，P-46；门命令写在状态锚里）。
- `AGENTS.md`（`AGENTS写权`）：默认归 user；user 在消息里明确授权时棘轮可改（实测例：v5 边界令 m00719 后的角色节与发权段同步，2026-11-10），改动必须同 commit 上架并在 `RATCHET` 记行；未授权时只提议不改。
- 推后必验：`git rev-parse --short origin/main` 写进 `ROSTER` §4；`grep "^version:"` 读数写进 `ROSTER` §1.4（`版本门读数=宣布值`：改班规必须同 commit bump）。
- 收权（`收权`）：user 一句「流程层不许直推」即回到他合并；他可随时 revert。
- 推前必跑 `TABLE_CHECK`（`表格列数校验`，P-35）与产品面 grep 为空校验。

## 7. 流程 C：吸收卡点（只在轮间）

1. `git fetch origin main && git rebase origin/main`；读 `PAIN_QUEUE` 的 pending 行 + 来信（`inbox` 索引 → 点名 `--full`）。
2. 逐条判定：吸收 / 合并进已有条目 / 证据不足退回（状态写回队列）。
3. 改文件：动过的 skill `version:` +1；门集内五份流程契约常需同步 bump；每条改动带「实测」注记（命令 + 结果 + 日期）。
   3a. `标记可命中`：往 `RATCHET` 登记新 grep 回归标记前，先跑 `grep -rlE -e '<标记>' .pi/skills docs/acceptance` 非空才登记——标记名必须取正文里能整串命中的串（实测 v3 标记 `流程层必须推到 origin` 因反引号切词长期假阴性，P-54）。
   3b. `班规单一源`：同一条程序在两份以上契约里出现时，只允许一处全文 + 其余指针。门集名单与门命令＝名册状态锚；信箱/回执/路由协议全文＝通用层 §4；升级地址＝名册 §2。项目契约只写角色参数（P-56）。
4. `RATCHET` 加版本行 + 读数行；`ROSTER` 更新 §1.4 生效版本与 §4 上架 sha。
5. `TABLE_CHECK` 绿 + 产品面 grep 空 → 按 §6 **上架六步**（`上架顺序`，不得即兴）推 `origin/main`。
6. 重写 `ROSTER` §0 状态锚（≤6 行；队列计数按 `pain.md` 实际行数数）。
7. 每方各一封生效通知（前提＝两个 `轮末 <issue> <sha>` 信号齐，见 §5；正文=版本号+sha+**该方需要改变的动作 ≤5 条**+通俗解释），回执只需一行 `已读 version:<n> @<sha>`；两行回执记进 `RATCHET`。（`发信写法`：`orca orchestration send --from <自己Run> --to run:<对方Run> --subject … --body "$(cat <正文文件>)" --json`；`send` 无 `--body-file`，自造 flag 前先 `--help` 核对）
8. 补 `RATCHET` 棘轮读数行（列以 `ratchet.md` 该表现有表头为准，不在本文件重述 schema）；坏指标连续两轮不降 → 同 commit 写复盘段落。
9. 轮内他们按旧版跑完手头工作，不中途换版（`轮内冻结`）。

## 8. 棘轮规则（只向前的硬口径）

1. **只收紧，不放宽**：要放宽只能写「给 user 的提议」，不动文件。
2. **每条吸收留可复跑回归**：写不出回归（命令 + 输入 + 期望输出）→ 不算吸收。
3. **工具优先于文字**：能进脚本/门禁的固化成工具，skill 里只留指针。
4. **轮内冻结**：一轮开工到交工不改流程文件；改动只在轮间。
5. **版本单调**：改任一 skill 同 commit `version:` +1 并在 `RATCHET` 记行；上岗读数≠生效值即停。
6. **不得用「这次特殊情况」豁免**。
7. **流程层必须推到 `origin/main`**：只停在本地对别的会话等于不存在（实测 loop-2：本地 main 已有 v2 契约，`origin/main` 仍是旧版）。

## 9. 流程读数（每轮一组，写在 `RATCHET` 末尾）

列以 `docs/acceptance/ratchet.md` 棘轮读数表的现有表头为准（单一事实源，不在本文件重述 schema）。

坏指标（打回、卡点、自检失败）逐轮持平或下降；连续两轮不降 → 写复盘段落。

## 10. 技能自选（`技能自选`）

skill 由会话自行调用，契约只保证硬口径不被 skill 跳过。你常用：`writing-great-skills`（改班规前）、`research`、`grilling`（压自己的设计）、`qa`。

## 11. 停止条件（触发即停，上报 user）

- 要放宽既有检查或一票否决口径 → 只提议。
- 同一轮里两边报出不同流程版本 → 停，先统一。
- 卡点根因在产品代码或门禁本身 → 转监理判定，由监理派工人；你不动代码。
- 队列积压 > 10 条或连续两轮读数不清零 → 上报，先清账。
- 双盲或可见性任一不成立 → 立即停；发现同一角色多个存活会话 → 停，等 user 退役。
- 状态锚门命令五行读数不一致，或 ≠ `ROSTER` §1.4 登记值 → 先补推流程层再开工。

