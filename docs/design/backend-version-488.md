# 后端源码身份与缓存刷新（#488）

遵守 ADR 0009 的独立渠道和 ADR 0030 / #458 的进程所有权：本机制不下载更新、不启动或停止服务，只提示人工重启。

## 身份边界

`main.py` 在导入其它后端依赖之前导入 `app.backend_version`，立即冻结 `PROCESS_BACKEND_ID`。纯摘要算法位于 `app.backend_source`；刷新工具只导入纯算法，检查失败也不会阻断其它清理及失败汇总。它是本次进程导入起点的源码快照，不是请求时重读 `VERSION`、Git HEAD 或文件时间。`VERSION` 仍只用作原有发布版本显示。

v1 以 SHA-256 哈希以下有界源码族：根目录 `*.py`，以及 `app/`、`plan_ir/` 递归的 `*.py`。相对 POSIX 文件名排序；名称、实际文件字节分别以 8 字节长度前缀分帧。新增、删除、改名、内容改变均改变身份。没有 Git 或安装目录改变不影响同内容身份。

先以 lstat 排除符号链接、Windows reparse point/junction，再判定保护目录；保护路径只做词法绝对化，不能为了排除链接先 resolve 并触碰目标。跳过隐藏目录、`__pycache__`、`node_modules`、`data/assets/output/logs/tmp/runtime/projects/venv/browser-profiles` 和配置的运行目录、项目库。静态资源、运行数据、凭据、`VERSION`、测试和维护工具不入哈希。此身份只覆盖上述 Python 源码，不承诺检测依赖包、解释器或静态目录内的宿主扩展变化。

部署必须在启动前完成。不承诺在并发替换源码或链接的过程中获得原子跨文件快照；不声称 Python 可以热加载，也不以身份代替路由功能测试。启动快照无法读取则启动失败；请求时读取失败则明确未确认，不假装匹配。

## 公共合同

`GET /api/app-info` 保留 `name/version/repo_url/repository`，添加 `backend`，响应 `Cache-Control: no-store`：

- `schema: 1`
- `loaded: "sha256:<64 lowercase hex>"`：冻结的启动快照
- `source: "sha256:<64 lowercase hex>" | null`：此刻磁盘源码
- `restart_required: boolean | null`：两者是否不同；读盘失败为 null

不公开文件名、目录、凭据、运行会话或异常详情。不改变 `/api/healthz`。

三个产品页的独立 `service-version.js` 用同源端点检查；加载、回到页面和每 30 秒重新检查，单次超时 4 秒。版本不一致显示持久的「服务需要重启」，指导托盘「重启服务」或在原窗口 Ctrl+C 后重新运行 `run.bat`。404 / 无 backend 字段视作未提供版本的旧服务，说明无法确认已加载新代码，并给相同人工操作。告警不自动消失、不自动重启；本 origin 的 localStorage 仅保存 stale/legacy 标志，重载及临时离线仍保留，验证匹配才清除。存储不可用时保留本页告警。未知网络/HTTP/格式错误只显示「服务版本未确认」，不冒充已证明版本不一致。文案来自中文词典，只用 textContent。

## 刷新入口

`tools/refresh-cache.ps1` 添加 `-ServiceUrl`（默认 `http://127.0.0.1:3000`），传到 `python -B -m tools.refresh_cache --service-url ...`。自定义端口必须显式提供本安装对应的本机 HTTP 基址。诊断固定访问 `/api/app-info`、3 秒超时，不用环境代理、不跟随重定向。

清理边界、静态资源戳的原子更新及旧失败标识保持不变；即使后端检查失败，也完成原有安全清理和资源戳刷新并合并失败。只有 running loaded、running source、本目录 source 三者相等且 restart_required=false 才返回 0，允许原「无需重启服务」成功文案。旧服务或版本不一致报 `backend_restart_required`，指导「后端已更新，需要重启服务」，返回 1。无法连接、读盘或合同不完整报 `backend_check_failed`，说明无法确认并返回 1，不报成功。纯静态修改不改变后端身份，仍能不停机刷新。

回归证据包括独立 checkout 的真实 PowerShell 退出码、微型 FastAPI v1→磁盘 v2→人工重新启动测试、冻结/实时摘要与保护目录/junction 测试，以及独立前端通知行为测试。

集成验收另外使用 3181 隔离实例和真实 `main.py`，不是微型服务：在导入后仅为该自有 checkout 加入无副作用路由，旧进程返回 404，身份显示不一致，原生浏览器持久显示「服务需要重启」，刷新工具返回 1。人工停止确切拥有的进程、另启自有隔离根后，同一路由返回 200，loaded/source 相等，浏览器告警消失，刷新工具返回 0。两个阶段截图均已实际读图；未操作用户 3000 服务。夹具按边界拒绝的偏好/注释等请求留有 403 警告，不声称控制台无警告。
