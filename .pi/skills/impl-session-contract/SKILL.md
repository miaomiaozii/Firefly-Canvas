---
name: impl-session-contract
version: 3
disable-model-invocation: true
description: 交付方与验收监理的交接协议。Use when 准备关闭某个 Issue 或打 ready-for-agent、收到 subject 含「验收打回」的邮件、要向监理提交一次交付。
---

# 交付方交接协议

你（含你派出的子代理）是交付方。判定权在监理（`.pi/skills/acceptance-supervisor/SKILL.md`）：它自己复跑门禁，你的汇报是索引，不是证据。

## 上岗前置（先验，缺就停）

1. **工作树由维护者用 `orca worktree create` 建好交给你**（带 `--branch issue/<N> --issue <N>`）。**不要自己 `git worktree add`**：那样建的树 Orca 不认，你的会话会退成后台游离句柄（`orca terminal show` 里 `tabId` 以 `pty:` 开头），维护者在界面里找不到你——监理轮当场失效。
2. 自检两条，缺一就上报维护者、不要开工：`orca worktree current --json` 不报 `selector_not_found`；`orca terminal list --json` 里按 handle 找得到自己且 `tabId` 是 UUID。
3. 信箱：`orca orchestration run-current --json`；没有就 `run-create --objective "delivery #<N>" --json`。**Run 必须在你自己会话里建**（维护者预建再转交给你会把邮件注入到维护者会话）。监理的 Run 由维护者粘给你。绑不上就 `orca orchestration run-use --id <SELF_RUN> --json`——**Run 没绑到本终端就收不到唤醒**（实测：监理的任务书在你绑定前送达，石沉大海）。
4. **流程契约取法（`流程契约取法`，2026-11-10 loop-2 实测）**：三份流程契约一律从 `origin/main` 读，**不读自己工作树那份**——`git fetch origin main && git show origin/main:.pi/skills/impl-session-contract/SKILL.md`；判据：`git show origin/main:.pi/skills/<契约名>/SKILL.md` 的 `^version:` 读数必须等于维护者本轮宣布的流程版本，读不到或读数不符 → **即停**上报维护者先推流程层。**不得用 `git merge main`/`rebase main` 到 `issue/<N>` 来取流程更新**——那些无关改动会被监理 `git diff --stat origin/main...HEAD` 算成你干的活（实测 `issue/310`@`16df89a` 上 v1/v2 的 14 项收紧全部不可见：`grep -n "^version:" .pi/skills/*/SKILL.md` exit 1、`grep -rlE "卡点上报|双盲三查|单写|上岗前置" .pi/skills` exit 1，该会话全程按旧口径跑）。你引用任何契约条文的行号与原文，都按本条取法。
5. **握手**：绑完 Run 立即给监理发一封在线回执（`send --to run:<监理完整 RunId（含 run_ 前缀）>`，正文：SELF_RUN、`orca terminal list` 里自己的 tabId、`orca worktree current --json` 的 path、HEAD commit、`run-current` 回读非 null、`origin/main` 侧流程版本读数），然后才等任务书。监理未收到回执不应发任务书。觉得“有信但没醒”先用 `orca orchestration inbox --json`（只读不消费）查，不要轮询 `check`（它会消费邮件）。

## 上岗档位

默认 `{REASON:007}{REASON:icode}`（agentic 操作员：多步工具编排 + 错误隔离 + 改→跑门禁→修到绿的自纠错循环），后续每条重复同一组合。选型是 2026-11-10 两轮六组合×两任务实测定的（见 `.pi/skills/reasoning-modes/SKILL.md`）；备选 `{REASON:ultra}{REASON:icoder}`，阶段拆分最清楚、“先提交再汇报”说得最明，但实测两轮都把正文写到截断，用它就需把输出预算提到 4000+。

**不加 `x` 前缀**：`007`、`house`、`musk`、`stark`、`coder` 都没有 x 版，写 `{REASON:x007}` 是无效令牌且不报错；`xultra` 实测会把正文挤空。`house` 不做默认档位：实测它把 House 剧本人格带进汇报（“蠢bug”“别让我再说第二遍”、医学比喻），还漏了“先提交”这一步。

**harness 档位用 `--thinking xhigh`**。`--thinking high` 对这几套模型是 `null`（`thinkingLevelMap` 里 high 与 max 为空），等于没设。`max_tokens` 单条上限 32768，重令牌实测单条能吃掉 11000+ token、单题 5–9 分钟（`xultra` 不存在，别写；要重就用 `xhyper`/`xlogic`）。先确保四段汇报能完整写完，再谈更重的档位。

## 提交一次交付

1. **先锁约束**：开工前把 Issue 正文与现有测试里写死的值（端口、阈值、档位名、目录名）列成一份约束清单，贴进计划。改动前先比对——实测两个档位组合都因为凭直觉改端口值，把场景弄挂而不自知。
2. **先提交**：改动提交到 issue 分支并记下 commit hash——未提交的改动监理验不了，也会被别的会话 `git clean`/`checkout` 清掉。`git status --porcelain` 必须为空，未跟踪文件也算脏。
3. **先跑基线再动代码**：开工前在**与交付同档位**（trial）跑一次基线门禁，归档为 `<EVIDENCE_ROOT>/<issue>-baseline-<短sha>-trial`，把红项清单（步骤名 + `verdict_reasons` 原文）逐条记录——本机存量红常驻（实测：`unit-contract-remainder-7`/`-10` 因 `cmd.exe` 归属 S0、`ui-integrity-evidence` 因 Chrome 156 vs 基线只接受 155），不先拿基线你就分不出“存量”与“你弄挂的”（实测交付方先跑 fast，到改完代码才发现存量红，白跑一轮）。**要跑基线就在主工作区干净 main 或另建的基线工作树上跑，不要在交付工作树里 `git checkout <基线 commit>`**——那会破坏被验收对象，也会让监理验不了你。
4. **自查**：交付前跑 `trial`（300 s，累计超集：跑 trial 就不用再跑 fast/domain），证据归档到监理指定的 `EVIDENCE_ROOT`。**可提交的口径是零新增红**（与基线证据逐条比对，差集为空），不是“无红”；`final_exit_code=124` 是超预算，不算过。门禁按组并行（默认 `FIREFLY_QUALITY_GATE_PARALLEL_WORKERS=8`）且组级 fail-fast（`tools/quality_gate.py:3740` `if group_failed: break`）——**尾部组（含 `tabbit-e2e-evidence`、egress）可能整组没排到**，你以为跑过了其实没跑。自查时核对证据里的已执行步骤清单，被测步骤缺席就补一轮串行：`FIREFLY_QUALITY_GATE_PARALLEL_WORKERS=1 python tools/quality_gate.py trial --repo-root <工作树> --evidence-dir <…>`——**串行轮在 trial 预算内跑不完**（实测 302.1 s → `final_exit_code=124`），它只证明步骤能执行，不是门禁判定；想要门内真覆盖尾部组，先清存量红或用 `release`（900 s）档。实测三轮 trial（基线/交付/监理）各 32 个已执行步骤里均无 `tabbit-e2e-evidence` 与 `egress-attribution`。
5. **门禁外端到端（改动影响前端或 agent 宿主时必跑）**：`bash tools/e2e_qa.sh`（只吃一个位置参数 `base_url`，默认 `http://127.0.0.1:3000`；退出码 = 失败场景数，`99` = tabbit-cli 不存在，`98` = 没有已打开的项目——先把画布打开再跑）。截图自动落到 `.pipeline/evidence/screenshots/`，基线比对 `python tools/e2e_shot_diff.py --strict`（默认已指向该目录与 `tools/e2e/baselines`）。**按改动范围跑对应场景脚本**：`bash tools/e2e/scenario_delete_undo.sh`（撤回链）、`scenario_mouse.sh`（鼠标）、`scenario_keys.sh`（快捷键）、`scenario_ux_rules.sh`（体验规则），`BASE` 用 `FIREFLY_BASE` 覆盖。完整入口 `python tools/e2e/full_e2e_test.py --baselines-dir tools/e2e/baselines --output-dir <EVIDENCE_ROOT>/<…>` 在 keys 套件红时会 fail-fast（实测：keys 里 3 场景 `page.screenshot: Timeout 30000ms` 直接卡住后面的鼠标套件），这时**直跑对应场景脚本留证**并在汇报里写明入口卡在哪个套件、错误原文（监理已按此口径接受过 P3=B）。首跑会自动新建 `tools/e2e/baselines/m*.png` 等未跟踪基线——那是改考卷（一票否决面），当场删掉并在汇报里声明（实测交付方删了 m1/m2/m4 三张）。飘的场景记“两轮各一次结果”，不要只报一次。

**别拿门禁绿当端到端证据**：门禁里的 `tabbit-e2e-evidence` 步在门内必然「环境未就绪 → exit 0」快速跳过（门内的临时 home 看不见 `tabbit-cli`），`scenario_mouse.sh` 更是不在它的名单里。监理会去证据里搜 `TABBIT-E2E 环境未就绪`，你少跑一次就会被当成缺证据打回。
6. **汇报**：

   ```bash
   orca orchestration send --to run:<监理的 Run> \
     --subject "#<issue> 待验收" --body <正文>
   ```

   监理的 Run 由维护者在激活时给你。你自己这一侧：`orca orchestration run-current --json`，没有就 `orca orchestration run-create --objective "delivery #<N>" --json`——监理打回要靠这个 Run 找到你。邮件由 Orca 主动送达，不要写脚本轮询 `check`（它会消费邮件）。
7. **关闭**：等监理签字。签字后由你按 `docs/agents/issue-tracker.md` 写结论评论并关闭 Issue，triage 标签由监理落。**关闭不等于合并**：代码可能还在 `issue/<N>` 上没进 main，`issue/<N>` 分支在维护者合并前不得删。

**汇报里每个数字必带来源，且必须来自已完成运行（`已完成运行`）**：时间、退出码、场景 PASS/FAIL、计数都要标上日志文件或证据目录路径；跑完的才叫数字，没跑完的写「待实测」占位——无来源的数字当作未验证宁可不写，把未跑完的项写成完成结果同样禁止（实测交付方在门禁外 E2E 跑完前就把暂定耗时/成败发进 #310 评论，内含不存在的“修复后重跑全 PASS”：1 条公开更正评论 + `scenario_mouse.sh` 重跑约 10 min + 监理首轮若采信即被污染）。发 Issue 评论用 `gh issue comment <N> --repo <REPO> --body-file <文件>`，引用评论 ID 前先 `gh issue view <N> --repo <REPO> --json comments --jq '.comments[]|{id:.databaseId,created:.created_at}'` 拿真 ID，别凭记忆写；关闭用 `gh issue close <N> --repo <REPO> --comment "..."`（`--help` 实测：`-c/--comment <文本>`，另有 `-r/--reason`、`--duplicate-of`；**没有** `-F/--body-file`，长文本先 `gh issue comment` 再 close）。自造 flag 前先 `--help` 核对（`--help 核对`），`docs/agents/issue-tracker.md` 里已有的写法照用，不要绕开。

汇报里另加两行：①**改动清单逐项对照 Issue 声明**（改了哪些文件，各自对应 Issue 里哪一条；Issue 没声明的改动要么删除要么先补声明）；②**断言与场景改动单独自证**：改动或新增 `tests/**` 与 `tools/e2e/**` 断言时，写清断的是什么行为终态、为什么不是放宽旧断言；修飘报测试时先给复现统计（跑 20 次挂几次），没复现就说没复现，不得先改断言。

**完成判定**：改动已提交、四段齐全（含末尾通俗解释）、证据落在 `EVIDENCE_ROOT`、Issue 状态与监理裁定一致。汇报正文为空即未完成——重档位下模型可能把预算全花在思考通道，加大预算或降档重发。

## 卡点上报（干活时也盯着流程本身）

遇到「不是代码问题，是流程没覆盖、或流程与实际不符」，当场报流程优化者，不要自己绕过就算完（你踩到的坑最多，不报就是下轮再踩一次）：

```bash
orca orchestration send --to run:<流程优化者 Run> --subject "#<issue> 卡点 <短标题>" --body "<四段>"
```

四段（缺一不排进本轮）：**现象**（命令原文 + 退出码/错误原文 + 时间戳或邮件 ID）/ **现行流程**（`<文件:行号>` 怎么说）/ **代价** / **最小改法**（加一条什么机器可判的条件）。**不要自己往 `docs/acceptance/pain.md` 写**——队列只由流程优化者**单写**（三个会话写同一个文件会互相覆盖，台账已被回滚过一次）；邮件正文就是这条卡点的真相，优化者收到后落一行并把邮件 ID 当来源。上报后**继续干活不等回复**；只有触发停止条件的卡点才停。轮中不改流程文件，改动在轮间生效。

## 什么时候停下来问

满三种就停，不要自己拍：

- **验收口径本身成了待改对象**（要改断言、改门禁、改 Issue 里的验收标准）——这属于改考卷，只有维护者能批。
- **Issue 未声明的改动变得必要**（如要改 `tests/**` 既有断言、`tools/quality_gate.py`、`tools/e2e/**`、`package*.json`）——先补声明与理由，再动。
- **出现产品取舍或两种都能过的方案**——转给监理，由它升级维护者。

停下来时发一封 `--subject "#<issue> 待决断"` 的邮件，列出 2–4 个选项及各自代价，等回复再动。

## 收到打回

按维护者指令处理：只修打回列出的项 → 重跑自查 → 汇报标"第 N 轮 + 本轮改了什么"。两轮未过就问监理要不要升级。验收口径不是交付方可改的对象。

**对裁定有异议时拿原文对，不要闷头认账**：先 `grep` 契约与 `docs/agents/issue-tracker.md` 原文（带行号），逐条写清“指控成不成立、原文用什么词”。实测监理自造“关闭归维护者”误判交付方越权，交付方引 `impl-session-contract/SKILL.md:40` 与 `issue-tracker.md:51` 申辩成功，监理公开撑回并记为正面观察。

## 让改动经得起一票否决

- 改动 `tools/quality_gate.py`、`tests/**` 断言、`tools/e2e/**` 场景断言、`.github/**`、`package*.json` 时：先让 Issue 声明这项改动，并在汇报里单列一行说明为什么非改不可。
- "按钮已存在"回答不了"功能是否可用"：按六维验收面的口径回答（Issue #294）。
- 汇报里只写本会话真跑过的命令与真拿到的结果。
