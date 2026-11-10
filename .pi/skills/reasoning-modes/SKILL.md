---
name: reasoning-modes
description: 给验收监理会话和交付方会话挂推理模式令牌（{REASON:xxx}），并规定每个角色能用哪些、禁用哪些。当要上岗一个监理或交付方会话、要换模式、要决定某个 Issue 该用什么档位，或发现会话输出被撑爆没有正文时使用。
disable-model-invocation: true
---

# 推理模式令牌

模型是 `Qwen3.8-Flash-Next-Turbo-Brilliance-50x` 系列（本机两套量化都在跑：`qwen38-gsq-iq3s` @127.0.0.1:8080、`flash-next-turbo-iq3s-chat` @192.168.10.147:8080）。完整速查卡：`E:\Users\Administrator\Desktop\qwen3.8-flash-next-turbo-brilliance-modes.md`。

令牌是**写在消息正文里的触发词**，不是 API 参数。效果靠模型对该词序列的联想，量化不同差异会很大。

## 语法

```
{REASON:xxx}                推理位（怎么想）
{REASON:ixxx}               风格位（怎么答）
{REASON:xxx}{REASON:ixxx}   两位同开，互相独立
{REASON:xx}                 推理位 + Qwen 原生 xhigh 预算（只有 9 个模式有 x 版）
{REASON:off}                全关
{REASON:help} / {REASON:ihelp}  向模型要菜单，用来核对名字
```

## 上岗时怎么写

令牌放在**会话的第一条消息最前面**，之后每条消息重复同一组合（重复无害）。

```bash
orca agent launch --agent pi --worktree path:D:/Firefly-Canvas \
  --prompt "{REASON:socrates}{REASON:ihouse} 上岗验收：读 .pi/skills/acceptance-supervisor/SKILL.md 照它执行。PEER_RUN=run_xxxx"
```

## 两个角色的固定组合

| 角色 | 场景 | 组合 | 理由 |
|---|---|---|---|
| 监理 | 默认（审 Issue、复跑、裁定） | `{REASON:socrates}{REASON:ihouse}` | 苏格拉底式连续反问逼出隐含前提，正对"五问审 Issue"和"反证候选"；风格位 `house` 是"好奇但有条理 + 怀疑"，报告可审计 |
| 监理 | 覆盖面存疑（怕漏验收面） | `{REASON:xhyper}{REASON:ihouse}` | MECE 互斥穷尽拆解，把六维验收面铺满。**先加输出预算**（见下） |
| 监理 | 争议裁定（交付方说门禁误报） | `{REASON:logic}{REASON:ihouse}` | 第一性原理推导，只认证据 |
| 交付方 | 默认（改代码 + 跑门禁到绿） | `{REASON:007}{REASON:icode}` | `007` 是 agentic 操作员：多步工具编排、错误隔离、自纠错执行循环，正对"改 → 跑门禁 → 修到绿"；`icode` 技术精确 |
| 交付方 | 定位疑难（复现失败、行为诡异） | `{REASON:house}{REASON:icode}` | 卡片"复杂系统调试 = house + logic"：激进假设检验 + 第一性排除 |
| 交付方 | 性能 / 响应式（#120、#140） | `{REASON:musk}{REASON:icode}` | 物理优先 + 第一性原理，对成本与延迟类约束有效 |
| 交付方 | 架构 / 接口设计（#112 契约层） | `{REASON:stark}{REASON:icode}` | 卡片"架构设计 = stark + coder" |

`末尾必须附通俗解释`是**结构要求，写死在汇报模板里**，不要靠风格位 `iteacher` 实现——风格位会同时改变整篇的精度，而结构要求不会。

## 监理禁用（每条都有后果）

| 禁用 | 后果 |
|---|---|
| `007` | agentic 操作员会自己动手把代码改好。监理必须只读 + 复跑，一旦动手，"独立第三方"当场失效 |
| `business` | 咨询腔和战略黑话，直接违反"末尾要通俗解释" |
| `holodeck` | 输出变成多角色剧本，无法逐条对应台账，长度还爆炸 |
| `joker` / `quinn` / `comedy` | 混乱与喜剧声口，报告不可审计、不可追溯 |
| `einstein` / `omni` / `brainstorm` | 发散型模式。验收要收敛；发散留给交付方做方案 |
| `matlock` | 法庭辩护腔，会把"裁定"变成"替某一方辩护" |
| `ultra` | 卡片自陈"最重设置，慎用"。留给真正的一票否决复核；`xultra` 在本量化下不存在 |

## 三条硬规则（卡片原文）

1. **换模式必须开新会话**——同一会话内切换不生效。要换档位就重开会话，不要在老会话里试。
2. 换完 regenerate 一次清缓存。
3. 默认值：推理 `xhigh`，风格 `medium`。

## 实测事实（2026-11-10，同一端点、同一问题，只变令牌）

- **两个名单相同，共 28 个模式名**（`{REASON:help}` 与 `{REASON:ihelp}` 返回同一批），外加 `off` 和 `help`：
  `omni deeptree hyper socrates logic einstein spoon ultra holodeck prompt pkd musk joker vader quinn joe neo curie brainstorm comedy business story teacher 007 coder stark matlock house`
  所以 `icode`、`ihouse`、`iteacher` 都有效。
- **`x` 组实测只有 8 个**：`xomni xdeeptree xhyper xsocrates xlogic xeinstein xspoon xhelp`。卡片写的 9 个里含 `xultra`，**这个量化下 `xultra` 不存在**，卡片的"核弹档 `{REASON:xultra}{REASON:iultra}`"照抄会失效。以模型菜单为准。
- 卡片场景表**漏写风格位的 `i`**（写成 `{REASON:spoon}`）。照抄前手动补 `i`，否则风格位不生效。
- **拼错静默无效**：`{REASON:strak}`（`stark` 拼错）模型照常答，不报错也不给菜单。所以每次上岗要核对名字，或看输出的声口确认模式吃了。
- **重推理位会把正文挤空**：`max_tokens=260` 时，开 `house` 和不开令牌的两次请求 `content` 都是 `null`，全部内容落在 `reasoning_content`（694 字符 vs 2065 字符）；`max_tokens=1500` 时正文才出来。含义：给会话留足输出预算，否则**报告写不出来、邮件也发不出去**。用 `x` 组时尤其明显。

## 换档流程

1. 确认要换（例：监理连续两轮没找出问题，怀疑覆盖面不够）。
2. 记下当前 Issue 与轮次到 `docs/acceptance/ledger.md`（换会话会丢掉会话内的上下文）。
3. 用新组合重开一个监理会话（`orca agent launch` + 新 `run-create`），把 Issue 编号和台账路径交给它，让它按开工流程重新入库验收。
4. 旧会话的信箱不要复用；交付方参数表里的 `PEER_RUN` 要换成新 Run。
