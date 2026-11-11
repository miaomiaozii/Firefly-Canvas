---
name: reasoning-modes
description: 推理模式档位契约：监理与工人两角色的固定组合、可用与禁用、换档流程与输出预算判据。
disable-model-invocation: true
version: 13
---

# 推理模式令牌

模型是 `Qwen3.8-Flash-Next-Turbo-Brilliance-50x` 系列（本机两套量化都在跑：`qwen38-gsq-iq3s` @127.0.0.1:8080、`flash-next-turbo-iq3s-chat` @192.168.10.147:8080）。完整速查卡：`E:\Users\Administrator\Desktop\qwen3.8-flash-next-turbo-brilliance-modes.md`。

令牌是**写在消息正文里的触发词**，不是 API 参数。效果靠模型对该词序列的联想，量化不同差异会很大。

## 语法

```
{REASON:xxx}                推理位（怎么想）
{REASON:ixxx}               风格位（怎么答）
{REASON:xxx}{REASON:ixxx}   两位同开，互相独立
{REASON:xx}                 推理位 + Qwen 原生 xhigh 预算（本量化只有 8 个模式有 x 版，见「实测事实」）
{REASON:off}                全关
{REASON:help} / {REASON:ihelp}  向模型要菜单，用来核对名字
```

## 上岗时怎么写

令牌放在**会话的第一条消息最前面**，之后每条消息重复同一组合（重复无害）。

会话按 `AGENTS.md` 的会话配方起（Orca 托管工作树 + 可见终端）。

**种子带档位（`种子带档位`，P-65）**：令牌必须随**种子**进入首条消息，种子随建随投；不得先建空会话再靠手打或事后补投（投递判据见通用层 §3 `发送判据`）。

```bash
# 例：监理（专属树 review/<N>，首选种子随建随投）
orca agent launch --agent pi --worktree path:<树> --surface terminal \
  --prompt "{REASON:hyper}{REASON:ihyper} 上岗验收：读 .pi/skills/acceptance-supervisor/SKILL.md 照它执行。" --json
# 退路：终端里起 pi，位置参数就是初始 prompt
pi.cmd --thinking xhigh "{REASON:hyper}{REASON:ihyper} 上岗验收：读 .pi/skills/acceptance-supervisor/SKILL.md 照它执行。"
```

令牌只管模式，不管 harness 的推理预算——两样都要给（只给令牌＝`--thinking` 没设）。

**harness 档位统一 `--thinking xhigh`**（会话内 `shift+tab` 循环，`ctrl+s` 存 settings）。实测依据：`~/.pi/agent/models.json` 里 `strata-*` / `llama-cpp-local` 的 `thinkingLevelMap` = `{minimal:null, low:"low", medium:"medium", high:null, xhigh:"xhigh", max:null}`——**`high` 与 `max` 是空值，写上去等于没设**。这套模型真正生效的只有 `low` / `medium` / `xhigh`。模型单条生成上限 32768 token。

## 两个角色的固定组合

| 角色 | 场景 | 组合 | 理由 |
|---|---|---|---|
| 监理 | 默认（审 Issue、复跑、裁定） | `{REASON:hyper}{REASON:ihyper}` | 同名双位：推理与回答都走 MECE 互斥穷尽，报告不重不漏。五组事到事评分里 10/16 最高，且是唯一会把不可测的验收标准**改写成可测形式**并映射到六维的（流程 A 第四问的核心能力），也是唯一主动处理 ADR 冲突的。代价：吃输出预算——会话输出预算 <1600 token 时正文会被截断（实测 1200 截断、1600+ 完整），此时改用下一行的组合 |
| 监理 | 输出预算紧 / 要最强的反证推理 | `{REASON:socrates}{REASON:ihouse}` | 苏格拉底式连续反问逼出隐含前提，事到事 8/16 但反证意识最好（“删掉这条断言等于拆掉报警器”），且两题都不断尾；预算不够或要复核某一裁定时用它 |
| 监理 | 争议裁定（工人说门禁误报） | `{REASON:logic}{REASON:ihouse}` | 第一性原理推导，只认证据 |
| 工人 | 默认（改代码 + 跑门禁到绿） | `{REASON:007}{REASON:icode}` | 事到事 11/16 并列第一，但是唯一两题都不断尾、无命令臆造、边界最干净的（只手动跑端到端，不提议把它接进门禁）；实测也不走捷径：只加等待，不动断言表达式 |
| 工人 | 备选（要它把长任务拆成可审计阶段） | `{REASON:ultra}{REASON:icoder}` | 阶段拆分与自设验证门槛最清楚、“PR 有 CI 红灯先修 CI 再汇报”说得最明，11/16 并列；但两轮实测都把正文写到 `finish=length`，用它必须把会话输出预算设到 4000+（settings 的 `maxTokens` 或启动参数；给不到就退回默认组合）|
| 工人 | 定位疑难（复现失败、行为诡异） | `{REASON:house}{REASON:icode}` | 激进假设检验 + 第一性排除。警示：`house` 思考可超 7000 字并挤断正文，仅在输出预算 ≥4000 时用 |
| 工人 | 性能 / 响应式（成本与延迟类约束） | `{REASON:musk}{REASON:icode}` | 物理优先 + 第一性原理，对成本与延迟类约束有效 |
| 工人 | 架构 / 接口设计（跨模块契约） | `{REASON:stark}{REASON:icode}` | 卡片"架构设计 = stark + coder" |
| 棘轮（流程优化者） | 默认（收卡点、改班规、写可复跑回归） | `{REASON:iteacher}{REASON:ihouse}` | 补空档：本节自 v1 起只给监理与工人定组合，`AGENTS.md` 却要求棘轮上岗带自己的组合（P-69）。选型依据＝风格位名单实测含 `iteacher`/`ihouse`（见「实测事实」）+ 棘轮硬要求「通俗解释」与「只认证据」；**未对棘轮做事到事评分**，属选型而非实测。棘轮不用 `007`（不改代码）也不用 `hyper`（不做验收）。种子已同步到名册 §1 上岗种子 |

`末尾必须附通俗解释`是**结构要求，写死在汇报模板里**，不要靠风格位 `iteacher` 实现——风格位会同时改变整篇的精度，而结构要求不会。

## 选型实测（2026-11-10，两轮六组合对测 + 两个独立评分代理）

方法：直连推理端点（`temperature: 0`、`max_tokens` 1600–3000），把真仓工件（`#294`–`#300`、`quality_gate.py` 四档与严重度、红项基线口径、`.pi/skills/**` 会触发 domain 档）写进题面；产物写进 `C:/Users/Administrator/backups/firefly-probe/`（`out_*.md`、`out2_*.md`）。两个 `oracle` 代理按 `RUBRIC.md` 事到事事到条打分，另有一轮反陷阱题（干净交付看会不会过度打回；飘报断言看会不会走捷径）。

评分（满分 16）：监理 `hyper+ihyper` 10、`spoon+ihouse` 10（两题均截断，剔除）、`ultra+icoder` 9、`socrates+ihouse` 8、`xhyper+ihyper` 8；工人 `007+icode` 11、`ultra+icoder` 11（两次截断）、`007+ihyper` 11（臆造 `make gate`）、`house+icode` 10（House 人格入报告）、`musk+icode` 10（提议把端到端接入门禁但未要求先声明）。

三条结论：

- **监理选 `hyper+ihyper`，工人选 `007+icode`**。监理的分数差在“能不能把不可测的验收标准改写成可测形式”；工人的同分裁决在“交不交得出完整正文 + 会不会越界改考卷”。
- **重档位（`x` 前缀）与剧本人格档位（`house`、`spoon`）默认被实测剔除**（例外只走固定组合表里明确列出并附预算警示的那一行）：推理吃掉 7000–8500 字后正文在半句处断，监理打回单缺复审条件段等于不可用；`house` 会把角色腔调写进汇报，`spoon` 会把 `{REASON:ihouse}` 原样回显进标签清单。
- **选型不靠档位，靠工件里的清单。** 六组合共同失分处：题面不给门禁口径时，**五组全部**推不出“trial 复跑 + `origin/main` 同档红项基线”；**五组全部**写不齐四段打回正文；**五组只有一组**用对仓库五标签，其余现场造词（`accept:conditional`、`accepted`、`needs-rework`、`needs-reproduction`、`epic`）。这些只能写在 skill 里，已经写了。

## 监理禁用（每条都有后果）

| 禁用 | 后果 |
|---|---|
| `007` | agentic 操作员会自己动手把代码改好。监理必须只读 + 复跑，一旦动手，"独立第三方"当场失效 |
| `business` | 咨询腔和战略黑话，直接违反"末尾要通俗解释" |
| `holodeck` | 输出变成多角色剧本，无法逐条对应台账，长度还爆炸 |
| `joker` / `quinn` / `comedy` | 混乱与喜剧声口，报告不可审计、不可追溯 |
| `einstein` / `omni` / `brainstorm` | 发散型模式。验收要收敛；发散留给工人做方案 |
| `matlock` | 法庭辩护腔，会把"裁定"变成"替某一方辩护" |
| `ultra` | 卡片自陈"最重设置，慎用"；一票否决复核走 `x` 例外（见前缀口径），不用 `ultra`；`xultra` 在本量化下不存在 |

## 前缀口径：只用 `i`，一般不用 `x`

- **`i` 是必须的**：第二个令牌不带 `i` 就是第二个推理位，等于同时开两种思考方式，卡片场景表那批范例全踩了这个坑。
- **`x` 默认不加**，三条理由：
  1. 原生推理预算已经是 `xhigh`（`PI_REASONING_LEVEL xhigh`），`x` 前缀的卖点只是再叠一层，收益存疑、成本确定；
  2. `x` 只存在于 8 个名字（`xomni xdeeptree xhyper xsocrates xlogic xeinstein xspoon xhelp`）。两个角色真正要用的 `007 / house / musk / stark / coder` **一个都没有 x 版**，写 `{REASON:x007}` 属于拼错，静默无效；
  3. 位越重思考越长，正文越容易被挤空（见实测）。
- **`x` 的唯一例外**：监理在①3 轮打回未过 ②一票否决复核 ③怀疑门禁本身有问题 这三种情况下，换会话用 `{REASON:xsocrates}` / `{REASON:xhyper}` / `{REASON:xlogic}`，并把输出预算提到 3000+。工人永远不用 `x`——它要的是执行循环，不是更长的思考。

## 三条硬规则（卡片原文）

1. **换模式必须开新会话**——同一会话内切换不生效。要换档位就重开会话，不要在老会话里试。
2. 换档后上岗自证：看新会话首条回复的声口是否符合组合（拼错令牌是静默失效的，见「实测事实」）。

## 实测事实（2026-11-10，同一端点、同一问题，只变令牌）

- **两个名单相同，共 28 个模式名**（`{REASON:help}` 与 `{REASON:ihelp}` 返回同一批），外加 `off` 和 `help`：
  `omni deeptree hyper socrates logic einstein spoon ultra holodeck prompt pkd musk joker vader quinn joe neo curie brainstorm comedy business story teacher 007 coder stark matlock house`
  所以 `icode`、`ihouse`、`iteacher` 都有效。
- **`x` 组实测只有 8 个**：`xomni xdeeptree xhyper xsocrates xlogic xeinstein xspoon xhelp`。卡片写的 9 个里含 `xultra`，**这个量化下 `xultra` 不存在**，卡片的"核弹档 `{REASON:xultra}{REASON:iultra}`"照抄会失效。以模型菜单为准。
- 卡片场景表**漏写风格位的 `i`**（写成 `{REASON:spoon}`）。照抄前手动补 `i`，否则风格位不生效。
- **拼错静默无效**：`{REASON:strak}`（`stark` 拼错）模型照常答，不报错也不给菜单。所以每次上岗要核对名字，或看输出的声口确认模式吃了。
- **档位与输出预算的关系**（同一验收问题，`max_tokens` 为变量）：

  | 请求 | 思考通道 | 正文 | 结果 |
  |---|---|---|---|
  | 不带令牌，260 | 694 字符 | **`null`** | 没有正文 |
  | `{REASON:house}`，260 | 2065 字符 | **`null`** | 没有正文 |
  | `{REASON:socrates}{REASON:ihouse}`，1200 | 1903 字符 | 999 字符 | 完整收尾 |
  | `{REASON:hyper}{REASON:ihyper}`，1200 | 3084 字符 | 837 字符 | **被截断**，正文只到一半 |

  含义：默认组合在 1200 token 内能出完整报告；`hyper` 双位会把预算吃满。正文为空或被截断时**报告发不出去、打回邮件也发不出去，而且不报错**。

## 换档流程

1. 确认要换（例：监理连续两轮没找出问题，怀疑覆盖面不够）。
2. **actor＝监理自己**：把当前 Issue 与轮次记进 `docs/acceptance/ledger.md`（换会话会丢掉会话内上下文；棘轮不写台账）。
3. **actor＝user**：用新组合重开一个监理会话（`AGENTS.md` 会话配方：Orca 托管工作树 + 可见终端 + `pi.cmd --thinking xhigh`），新 Run 在该会话自己里 `run-create`；把 Issue 编号和台账路径交给它，让它按开工流程重新入库验收。
4. 旧会话的信箱不要复用；工人参数表里的 `PEER_RUN` 要换成新 Run。
