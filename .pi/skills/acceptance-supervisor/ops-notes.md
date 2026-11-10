---
name: acceptance-supervisor-ops-notes
disable-model-invocation: true
---

# 监理契约运维笔记（`渐进披露`，P-51）

本文件只放**历史证据与演练实录**：事故经过、实测数值、复盘。约束与护栏一律在 `SKILL.md` 正文，本文件不立规矩、不得作为裁定依据（`历史证据上收`）。上收自 `SKILL.md` 的内容如下，SKILL.md 各节留指针。

## 会话可见性演练实录（2026-11-10 两会话演练）

- 反例：`git worktree add D:/ff-wt/w310` 建的工作树 Orca 不认（`orca worktree current` 报 `No Orca workspace contains the current directory`）；`orca terminal create --worktree path:<非托管路径>` 退化成后台句柄，`tabId` 形如 `pty:6a25…`，界面里没有页签——结果 user 只看到一个新会话。`orca terminal create` 自己的措辞就是「creates a visible terminal tab … falls back to a background handle if the UI cannot adopt it」。
- 实测：`orca terminal create --command "pi.cmd --thinking xhigh"` 返回 `tabId` = UUID（如 `8829d00e-…`）= 界面里有可见页签。
- 认人认 handle 的由来：实测监理会话改名 `监理-310` 被 pi 自己的标题刷新覆盖回 `π - Firefly-Canvas`，故 `orca terminal rename` 不可靠。
- 启动配方（user 执行，实测跑过一遍）：
  ```bash
  orca worktree create --name w<issue> --repo path:D:/Firefly-Canvas \
    --base-branch main --branch issue/<issue> --issue <issue> --setup skip --json
  # 返回 result.worktree.path（落点由 Orca 定）
  orca terminal create --worktree path:<上面那个 path> --command "pi.cmd --thinking xhigh" --json
  orca terminal create --worktree path:D:/Firefly-Canvas --command "pi.cmd --thinking xhigh" --focus --json
  ```
  `--agent pi` 不保证把 harness 档位带到 `--thinking xhigh`（`--effort` 未实测），故用 `terminal create --command`。

## 档位实测（单一事实源仍是 reasoning-modes）

- 实测 `~/.pi/agent/models.json` 里 Strata/Qwen 各档 `thinkingLevelMap` 只有 `low/medium/xhigh` 有值，`high` 与 `max` 为 `null`。
- 实测 `xhyper` 打回正文截断、`xspoon` 两题都截断，还有一个把 `{REASON:ihouse}` 原样回显进报告。
- 实测输出预算不足时正文直接为空；1200 token 只够默认组合出一份完整报告。

## 程序性裁定误判实录（对应 SKILL.md「程序性裁定必须先引原文」）

- 监理在裁定评论里写「合并与关闭归维护者」，据此判工人「越权关闭」；实际契约与 `docs/agents/issue-tracker.md` 写明「监理签字后由工人写结论评论并关闭」。代价：2 条公开评论 + 3 封邮件 + 4 分钟返工，还差点改掉对方正确行为。
- 教训：引用契约用节名，不用行号（行号随每次契约改动漂移，P-52）。

## flaky 实录（对应 SKILL.md「飘两轮制」）

- `scenario_mouse.sh` m4 一轮 `MARQUEE_BOX_MISSING`、次轮全 PASS；工人侧 m3/m5/m6 的 `page.screenshot: Timeout 30000ms` 在监理侧未复现——均按两轮制处理。

## 关闭≠合并实录（对应 SKILL.md「关闭 ≠ 合并」）

- #310 已 CLOSED，`16df89a` 只在 `issue/310` 分支上；台账行当时未写 `merge-pending <sha>`。

## 发信参数观察（未与契约结论对齐，待核）

- 监理契约旧「收发邮件」节写「不要加 `--from` / `--from-session` / `--run`」；2026-11-10 棘轮发 v10/v11 生效通知时显式带 `--from run_c40189b45d94`，两次 `send` 均成功（msg_c0ae5ab63a45 / msg_a8b74a2db804）。两条证据未对齐：可能 `--from` 仅在发件 Run 已绑定当前终端时安全。契约口径未改（不放宽），待取证后再校准（`--help 核对`）。
- 启动配方实录（user 执行）：`orca worktree create --name w<issue> --repo path:D:/Firefly-Canvas --base-branch main --branch issue/<issue> --issue <issue> --setup skip --json` → `orca terminal create --worktree path:<返回 path> --command "pi.cmd --thinking xhigh" --json`（返回 tabId=UUID 即可见）；监理侧 `--worktree path:D:/Firefly-Canvas ... --focus --json`；收尾 `orca terminal close --terminal <handle>` + `orca worktree rm --worktree path:<path> --force --delete-branch`。
