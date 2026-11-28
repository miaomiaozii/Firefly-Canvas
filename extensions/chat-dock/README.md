# Firefly 对话采集（#515）

这是一个 Chromium Manifest V3 扩展。在普通网站标签页中手动采集文字和图片，不替换网站界面或会话。支持的顶层站点（仅标准 HTTPS origin）：`chatgpt.com`、`gemini.google.com`、`chat.deepseek.com`、`chat.qwen.ai`、`www.doubao.com`、`www.kimi.com`、`yuanbao.tencent.com`。

## 安装和使用

1. 在 Chromium 的扩展管理页开启开发者模式，从 `extensions/chat-dock/` 加载已解压的扩展。扩展 ID 为 `ecbjgicacjhegoijmnkijlgeagafgogf`。刷新已打开的对话和画布标签页。
2. 打开 Firefly 的 `/static/smart-canvas.html` 并加载项目。默认服务地址为 `http://127.0.0.1:3000`；使用其他地址时，在扩展「选项」中填入完整的 HTTP(S) origin，点击「授权并保存」，然后刷新画布。只填协议、主机和可选端口；不用填页面路径。
3. 选中非编辑区的文字并点击「对话采集」。菜单可「新建文本节点」或「填入生图节点提示词」；后者需要在 Firefly 中选中一个可编辑的生图节点。画布选区发生变化后，可以重新打开菜单更新状态。悬停在已经加载的图片上，再点击「复制到画布」可新建图片节点。
4. 只使用配置的完整 origin 和精确画布路径（允许查询字符串或片段）。有多张匹配画布时，仅在其中恰好一张为活动标签页时选它；否则不给任意标签页发送数据。若提示画布未就绪，先确认已加载项目、已刷新正确的画布标签页且保留了浏览器站点访问授权。

## 在画布旁打开对话面板

从扩展「选项」点击「打开画布与对话面板」。Firefly 从已授权的服务地址打开项目列表；选择项目即可进入画布。对话面板可左停靠、右停靠或浮动。浮动使用移动和缩放按钮，停靠时可拖动分隔线；这些手柄也支持方向键。收起即可让画布使用全宽。切换站点只更换网页，位置、尺寸与收起状态在同一入口共用。扩展选项入口与同标签页入口分别记忆布局和站点；首次通过同标签页进入时沿用普通入口的布局尺寸，但仍从收起状态开始。旧设置按上次选中的站点保留布局（#521）。

豆包、Kimi 和元宝的公开入口可直接显示在面板内。站点策略可能变化，登录状态不保证可在面板中使用。ChatGPT、Gemini、DeepSeek、通义会拒绝被嵌入，默认只在普通标签页打开；可在扩展「选项」的「在对话面板内显示」里逐站开启（#515，用户 2026-11-28 裁定），开启后扩展用 declarativeNetRequest 只对「顶层是本扩展页面」的子框架去掉 `X-Frame-Options`，ChatGPT、DeepSeek、通义的整条 `Content-Security-Policy` 被替换为 `frame-ancestors chrome-extension://<扩展 ID>`，因此它们在面板内失去站点自带的全部内容安全策略（含防 XSS 的一层）；Gemini 只去掉防嵌入头，CSP 保持原样。普通标签页和其他网页中的这些站点不受影响。关闭开关会先撤销规则，再更新面板。这种用法可能不符合站点条款。所有站点均保留「在新标签页打开」。豆包、Kimi 和元宝，以及已在选项里开启面板显示的 ChatGPT、Gemini、DeepSeek、通义，支持面板内手动采集：先在同一外壳的 Firefly 项目列表打开一张画布，再使用与普通标签页相同的采集控件。只投递到这个外壳内的画布；画布未就绪时不会改投其他标签页。四个需开关的站点只在各自开关开启时可在面板内采集，后台每次投递前都会重新读取开关；关闭后面板不再显示该站点，采集也随之停止（用户 2026-11-28 要求）。

## 边界和隐私

- 选择和悬停只在当前标签页内显示控件。真实点击打开文字菜单时，只查询能否追加，不发送所选文字。真实点击采集操作才发一份捕获数据，不重试、不广播。扩展只持久化 Firefly origin 与面板布局，不记录选区、图片、会话内容或用户意图；成功后内容由 Firefly 按画布项目规则保存。扩展不代打字、发送对话、遍历会话或写入网站页面数据；除上面用户逐站开启的面板显示规则外，不改写任何响应头；无外部消息入口，不直接读写 Cookie。面板显示规则需要这四站的主机权限（declarativeNetRequest 改写响应头要求显式主机权限），扩展不借此读取站点数据。面板采集和顶层采集独立选路；面板采集只读取面板显示开关来决定四个需开关站点能否采集，不为采集新增扩展权限。
- Chromium 的站点访问授权以主机为单位，同一主机授权可能覆盖其他端口。首次仅请求扩展所列站点和默认回环服务；其他画布主机由用户通过选项授权，不申请通配站点访问。后台选路与画布脚本均检查完整 origin（含端口）。若自定义站点权限被撤回，不回退到其他画布。为非本机服务授权前请确认该 Firefly 服务可信，优先使用 HTTPS。
- 仅处理当前采集文档中已加载的普通 `<img>`，不深入其他子框架，也不处理 CSS 背景、canvas 或视频。读同源 `blob:`、`data:` 或由页面同源/CORS 允许读取的 HTTPS 图片；只在点击时发起读取。同源 HTTPS 图片使用页面已有会话；跨源 CORS 读取不带凭据。两者都不发送 referrer、也不跟随重定向；目的地址来自点选时的 `img.currentSrc`，点击时复核。无后台代理、无额外 CDN 权限。若图片无法读取，可以右键复制图片，再到画布按 Ctrl+V 粘贴。8 秒读取限时；PNG、JPEG、GIF、WebP 最多 8 MiB 原始字节，保持原二进制，不重编码。文字最多 20,000 个 UTF-16 代码单元。
- 扩展通过同窗 `postMessage` 将数据交给 Firefly，不向外部网站或服务上传捕获的数据。画布 origin 内的其他脚本也可看到该消息；这不是对画布同源脚本的身份认证。开放 Shadow DOM 用于隔离样式和测试，不把宿主页面当作可信输入源。

## 协议与验收定位

`chat-content.js` → 内部 `background.js` → 一个 Firefly 文档的隔离脚本 `canvas-relay.js` → `window.postMessage(data, location.origin, [port2])`。画布通过该 `MessageChannel` 回复，隔离脚本从 `port1` 转回 `{ok: boolean, canAppend?: boolean, error?: string}`。普通标签页仍只投递给顶层画布。面板投递需匹配该标签页的顶层扩展外壳，并确认来源和目标分别是外壳当前的两个直接子框架；确认后按浏览器文档 ID 投递，不用会被下一次导航复用的框架 ID。用于核对框架的探测不包含任何采集内容。

`protocol.js` 约束各动作字段；无额外包装字段：

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
node --test extensions/chat-dock/tests/browser.test.cjs extensions/chat-dock/tests/dock-shell-browser.test.cjs
node --test extensions/chat-dock/tests/framing-browser.test.cjs
```

浏览器测试需要项目 Playwright 依赖和其 Chromium 二进制（也可用 `FIREFLY_PLAYWRIGHT` 指向已安装的 Playwright 模块）。测试拦截浏览器请求并用本地 fixture 响应，不访问实际对话网站或 Firefly 服务。自定义站点的授权拒绝/撤销走模拟 API；浏览器测试用已授权回环主机的另一端口验证真实选项页和动态转发注册。面板测试验证正式包的三个停靠入口、七个普通标签入口、三站文字/图片只到同一外壳（含自定义端口、不就绪、重复目标、普通网页和嵌套框架拒绝），以及离线 fixture 上的允许嵌入和浏览器拒绝两种结果；拒绝用的安全头保持原样。可设置 `FIREFLY_DOCK_SCREENSHOTS` 保存面板截图。`framing-browser.test.cjs` 用 `openssl`（可用 `FIREFLY_OPENSSL` 指定）生成临时自签证书，起本地 HTTPS 服务并通过 host-resolver 映射站点域名；Playwright 拦截生成的响应不经过 declarativeNetRequest，所以这一项不能用拦截 fixture。
