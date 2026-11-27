# Firefly 对话采集（#515）

这是一个 Chromium Manifest V3 扩展。在普通网站标签页中手动采集文字和图片，不替换网站界面或会话。支持的顶层站点（仅标准 HTTPS origin）：`chatgpt.com`、`gemini.google.com`、`chat.deepseek.com`、`chat.qwen.ai`、`www.doubao.com`、`www.kimi.com`、`yuanbao.tencent.com`。

## 安装和使用

1. 在 Chromium 的扩展管理页开启开发者模式，从 `extensions/chat-dock/` 加载已解压的扩展。扩展 ID 为 `ecbjgicacjhegoijmnkijlgeagafgogf`。刷新已打开的对话和画布标签页。
2. 打开 Firefly 的 `/static/smart-canvas.html` 并加载项目。默认服务地址为 `http://127.0.0.1:3000`；使用其他地址时，在扩展「选项」中填入完整的 HTTP(S) origin，点击「授权并保存」，然后刷新画布。只填协议、主机和可选端口；不用填页面路径。
3. 选中非编辑区的文字并点击「对话采集」。菜单可「新建文本节点」或「填入生图节点提示词」；后者需要在 Firefly 中选中一个可编辑的生图节点。画布选区发生变化后，可以重新打开菜单更新状态。悬停在已经加载的图片上，再点击「复制到画布」可新建图片节点。
4. 只使用配置的完整 origin 和精确画布路径（允许查询字符串或片段）。有多张匹配画布时，仅在其中恰好一张为活动标签页时选它；否则不给任意标签页发送数据。若提示画布未就绪，先确认已加载项目、已刷新正确的画布标签页且保留了浏览器站点访问授权。

## 边界和隐私

- 选择和悬停只在当前标签页内显示控件。真实点击打开文字菜单时，只查询能否追加，不发送所选文字。真实点击采集操作才发一份捕获数据，不重试、不广播。扩展只持久化 Firefly origin 设置，不记录选区、图片、会话内容或用户意图；成功后内容由 Firefly 按画布项目规则保存。扩展不代打字、发送对话、遍历会话或写入网站页面数据；无 iframe、DNR、CSP/响应头修改或外部消息入口，不直接读写 Cookie。
- Chromium 的站点访问授权以主机为单位，同一主机授权可能覆盖其他端口。首次仅请求扩展所列站点和默认回环服务；其他画布主机由用户通过选项授权，不申请通配站点访问。后台选路与画布脚本均检查完整 origin（含端口）。若自定义站点权限被撤回，不回退到其他画布。为非本机服务授权前请确认该 Firefly 服务可信，优先使用 HTTPS。
- 仅处理顶层页面中已加载的普通 `<img>`，不抓取 iframe、CSS 背景、canvas 或视频。读同源 `blob:`、`data:` 或由页面同源/CORS 允许读取的 HTTPS 图片；只在点击时发起读取。同源 HTTPS 图片使用页面已有会话；跨源 CORS 读取不带凭据。两者都不发送 referrer、也不跟随重定向；目的地址来自点选时的 `img.currentSrc`，点击时复核。无后台代理、无额外 CDN 权限。若图片无法读取，可以右键复制图片，再到画布按 Ctrl+V 粘贴。8 秒读取限时；PNG、JPEG、GIF、WebP 最多 8 MiB 原始字节，保持原二进制，不重编码。文字最多 20,000 个 UTF-16 代码单元。
- 扩展通过同窗 `postMessage` 将数据交给 Firefly，不向外部网站或服务上传捕获的数据。画布 origin 内的其他脚本也可看到该消息；这不是对画布同源脚本的身份认证。开放 Shadow DOM 用于隔离样式和测试，不把宿主页面当作可信输入源。

## 协议与验收定位

`chat-content.js` → 内部 `background.js` → 单一 Firefly 顶层标签页的隔离脚本 `canvas-relay.js` → `window.postMessage(data, location.origin, [port2])`。画布通过该 `MessageChannel` 回复，隔离脚本从 `port1` 转回 `{ok: boolean, canAppend?: boolean, error?: string}`。`protocol.js` 约束各动作字段；无额外包装字段：

| `action` | 数据键（所有动作均有 `channel: 'firefly.chat-capture.v1'`、`action`、`site: <精确发送者 hostname>`） |
| --- | --- |
| `status` | 无其他字段 |
| `append-prompt`、`new-text` | `text` |
| `new-image` | `mime`（`image/png`、`image/jpeg`、`image/gif`、`image/webp`）、`bytes`（原二进制的 Base64） |

UI 宿主为 `#firefly-chat-capture-host`，使用开放 Shadow Root（Playwright CSS locator 可直接穿透）。稳定内部 ID：`#selection-chip`、`#text-menu`、`#append-prompt`、`#append-reason`、`#new-text`、`#image-chip`、`#feedback`；使用真实鼠标/键盘输入，页面的 `element.click()` / `dispatchEvent()` 不会触发采集。选项页为 `options.html`，输入框 `#firefly-origin`。

## 测试

从仓库根目录运行：

```sh
node --test extensions/chat-dock/tests/protocol.test.cjs
node --test extensions/chat-dock/tests/browser.test.cjs
```

浏览器测试需要项目 Playwright 依赖和其 Chromium 二进制（也可用 `FIREFLY_PLAYWRIGHT` 指向已安装的 Playwright 模块）。测试拦截浏览器请求并用本地 fixture 响应，不访问实际对话网站或 Firefly 服务。自定义站点的授权拒绝/撤销走模拟 API；浏览器测试用已授权回环主机的另一端口验证真实选项页和动态转发注册。
