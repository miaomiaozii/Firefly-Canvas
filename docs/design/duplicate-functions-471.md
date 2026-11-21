# #471：重复顶层函数守卫

本票只落实 #367 第 1 步；不删除副本、不选择运行时实现，也不改产品代码。

## 扫描口径

入口：`tests/test_duplicate_top_level_functions.py`。唯一可编辑白名单：
`tests/fixtures/duplicate_top_level_functions_471_baseline.json`。

- 当前源码只取 `git ls-files --cached --others --exclude-standard -z -- '*.py'`：
  所有已跟踪及未忽略的未跟踪 Python 文件，**包括 tests、tools、根目录、plan_ir、Git 可见的 .qa**。
  不自行遍历磁盘，不扫描被忽略的 .local/vendor/node_modules 或已安装依赖。
- 每个模块只取 `ast.Module.body` 直接包含的 `FunctionDef` / `AsyncFunctionDef`。
  类方法、函数内定义、if/try 等语句内部定义均不计入；不执行或导入被扫描模块。
- 同名函数位于至少两个不同模块才组成重复组；一旦成组，其中每次定义均保留。
  成员标识为 `(仓库相对模块路径, 该模块内同名函数从 1 开始的序号)`，不是行号。
  因此同文件再添一份不能绕过守卫，行号移动、普通注释和格式调整不会消耗额度。
- 按 Python 编码声明解析原始字节，语法/编码错误直接失败；不做容错解码。
  拒绝不安全路径、符号链接、Windows reparse point、非普通文件及树外路径。
  Git 仍列出的已删除文件不读取，以允许删除源码收敛债务；其它读取错误不吞掉。

当前首次守卫父树 `c5dda98fa022a22b4b6f46802c5e340313bd85bf` 共 568 个 Python 文件：
**315 个重复名、949 个模块成员、951 次函数定义**。新增守卫模块后为 569 文件，重复计数不变。
这是 #471 整合裁定批准的首次快照：继承 `02bb1b8` 一侧已经合入的源码债务，
由父树自动生成并 autosquash 回首次守卫提交，不是守卫合入后的白名单扩容。
两次额外定义分别是 `tests/test_agent_node_seed_354.py` 的 `_loop`、`agent_host_manager_singleton` 第 2 次定义。

票面的“约 179”是旧调查估计，不能用来限缩范围。当前 `app/ + main.py` 子集为 191 名/385 模块成员；
加入 tools 后为 197/420；所有 Git 可见文件才是 315/949。这里的差额主要来自测试、工具及其它仓库模块，
不是解析失败或误把类/嵌套函数当顶层。全仓无解析错误。

## 白名单为什么不能增长

设实际重复成员为 `D`，JSON 白名单成员为 `W`，原始历史源码的重复成员为 `H`：

1. 最初 bootstrap 使用常量 `ORIGINAL_CEILING_SOURCE_REVISION` 固定历史修订 `4dccf3c`；
   已有首次提交后，从 Git 历史找到白名单第一次引入的父提交作为不可增长的源码上限。这样 rebase 后仍使用同一阶段的父树，
   不依赖已不可达的旧提交；历史缺失时不回退到当前 HEAD。JSON 没有可编辑的 ceiling 字段。
   实际守卫只读 `git ls-tree -rz --full-tree`，批量 `git cat-file --batch` 读取上限树全部 Python 源码，
   重新生成 `H`，强制 **W ⊆ H**。原始上限不是当前 JSON 的哈希，也不是当前源码。
2. 强制 **D = W**。新增组、已有组增加模块/同模块增加定义、删除后遗留整组/成员均失败。
   重复消失为单模块时必须删除整组，不保留已经不重复的“最后一个成员”。
3. 仅有原始上限仍可能把未来已退役成员重新添回。因此另用
   `git log --full-history --format=%H -- <白名单路径>` 找出所有可达的已提交白名单版本，
   一次 batch 读取，强制 `W` 是每个历史白名单的子集。即使扩大后的 JSON 已经成为 HEAD，
   更早的缩减仍会阻止复活。初次引入白名单前无历史版本，仍由固定 `H` 约束。

成员允许减少；函数体、分类依据和指纹允许随源码更新，不固定整份 JSON 哈希。
例：三模块变两模块时同步删成员；两模块变一模块时同步删整组。
保留白名单文件（可以是空 groups），不要删除后重建以抹去历史。

正常 Git 子进程为 6 次；已有白名单历史时为 7 次，与源码文件数量无关。
`subprocess.run(input=...)` 内部使用 `communicate()` 同时写入/排空管道，避免 batch 管道互锁。
最初完整目标文件 42 项实测约 4.3 秒；补充首次引入/rebase 边界测试后的结果另见批次交付记录。

## 分类是源码证据，不是运行时等价证明

每组都有 `category`、`basis` 和逐成员 `ast_sha256`。判断使用
`ast.dump(..., include_attributes=False)`，含签名、装饰器、注解、类型注释和文档字符串，
排除位置及普通注释。指纹只验证描述没有过期，不参与历史成员上限。

| 标签 | 本守卫的严格含义 | 当前组数 |
| --- | --- | ---: |
| 完全相同 | 所有函数 AST 完全相同；**不证明模块全局绑定或运行时行为相同** | 108 |
| 只差全局变量访问方式 | 必须证明仅已知 main/global 访问归一化不同，无其它 AST 差异 | 0 |
| 逻辑已经分叉 | 存在不同函数 AST，且没有上述仅全局访问差异的证明；保守的**结构分叉**，不声称语义已证不同 | 207 |

本实现不做任何属性剥离或访问归一化，第二类有意为零；不会把任意 `obj.x`、局部遮蔽、
带副作用 `_app_main()`、动态 `_svc._main(...)` 等冒充裸全局变量。
AST 相同但各模块 `VALUE` 不同仍只说明“函数 AST 完全相同”，不说明返回值相同。
这也解释了本票分类数量为何不沿用 #367 旧调查的 92/71/16。

已只读抽样核对：

- `app/core.py` / `app/routers/api_project_library.py` 的 `_agent_clean_params`、
  `_agent_forbidden_value`、`_agent_public_task`：函数 AST 确实相同，属第一类。
- `app/services/agent_batch.py` / `app/services/agent_generation_retry.py` 的 `_api`：
  相同的延迟 import/return 包装器，同属第一类，但不推导 import 的运行时结果。
- `app/routers/api_storage_settings.py` 的 `load_storage_settings` 委托给 `_app_main`，
  而 `main.py` 是实际读取实现：结构不同，不能标为“仅全局访问不同”。
- `default_prompt_libraries` 的路由版 `_app_main.now_ms()` 与 services 版 `_svc._main('now_ms')()`：
  不把动态查找、绑定及副作用当作已证相同，保守列第三类。

## 更新与验证

首次生成（只允许创建不存在的文件，且先检查历史成员上限）：

```text
python tests/test_duplicate_top_level_functions.py --write-baseline
```

首次守卫尚未合入 main、整合 rebase 改变其父树时，必须先经 review 明确裁定：
仅从该父树重新运行 `_dup_guard_historical_sources` → `_dup_guard_inventory` → `_dup_guard_baseline`，
不得手工编辑初始快照，再将修正 autosquash 回首次守卫提交。交付逐项核对父树/首快照、
HEAD 相对父树的成员增减和指纹来源；这不是给后续提交扩大额度的入口。

后续源码删除或函数体调整，用 `_dup_guard_baseline(_dup_guard_inventory(...))` 查看候选，
审查后精确修改 JSON 的删项/分类/依据/指纹；不要通过换修订、改守卫或忽略源码放宽成员。
`_dup_guard_violations` 是独立纯比较函数，夹具覆盖增组、增模块、增次数、同时扩白、
历史退役成员复活、陈旧项、正常缩减、分类/指纹过期、局部遮蔽、解析和路径失败等。

定向验证使用隔离包装入口；整批 full/fast 结果单独留档：

```text
PYTHONUTF8=1 python D:/ff-qa/404-405-326/run_pytest.py guard471-<唯一标签> tests/test_duplicate_top_level_functions.py
```

真实未跟踪夹具在 `tests/fixtures/_duplicate_guard_471_owned_probe.py` 新增 `_app_main` 后，
原守卫报告新重复组并失败；只删除该自有夹具后恢复通过。原始红/绿日志及统计在
`D:/ff-qa/404-405-326/471/` 的审计索引，完整日志由包装器存放在同级 `runs/guard471-*`。
不访问用户项目、鉴权文件或模型，不进行生图/视频/模型 turn。

## 边界

这是同名源码债务守卫，不是语义克隆检测器，不检测改名后的复制、条件定义或运行时猴补丁。
依赖 Git、首次引入的父树及完整的相关白名单历史；缺失历史/对象、损坏 batch 均失败，不跳过。
应在真实仓库运行，不能在缺少 Git 历史的导出目录把失败改成通过。
AST 元数据随 Python AST 版本变化可能需要审查并刷新指纹（本轮 Python 3.12.10）。
它不能防御故意修改守卫/上限常量、重写 Git 历史或并发恶意替换文件；这些仍需代码审查。
本票未改产品代码，未进行后续副本清理或 #441。
