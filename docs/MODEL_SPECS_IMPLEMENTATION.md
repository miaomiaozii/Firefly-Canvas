# 模型参数规范系统实现总结

**实现日期**: 2026-08-25
**状态**: ✅ 已完成并验证

## 实现内容

### 1. 核心文件：`app/data/model_specs.json`
**用途**: 模型参数的单一数据源（Single Source of Truth）

**结构**:
```json
{
  "_schema_version": "1.0",
  "chat_models": {
    "model-id": {
      "display_name": "显示名称",
      "api_type": "responses | chat_completions",
      "supports_vision": true|false,
      "parameters": {
        "param_name": {
          "type": "boolean | number | integer | enum",
          "default": "默认值",
          "options": ["可选值列表"]  // 仅 enum 类型
        }
      },
      "notes": "备注"
    }
  },
  "image_models": { /* 同上，增加 async_mode */ },
  "video_models": { /* 同上 */ }
}
```

**当前收录模型**（2026-08-30 验收时实数）:
- **对话模型**: 16 个（GPT-4o / GPT-4.1 / GPT-5 系列、Claude 系列、Gemini、Qwen3 系列）
- **图像模型**: 20 个（nano-banana 系列、gpt-image 系列、Gemini Flash Image、Flux、Qwen-Image、Seedream、Grok-Imagine）
- **视频模型**: 15 个（Seedance 2.0/2.5 系列、Sora-2、Kling、Hailuo、PixVerse、即梦 CLI seedance2.0 别名）

> 注：`app/data/model_specs.json` 为仓库跟踪文件（.gitignore 中显式 `!` 放行），
> 干净克隆即可使用；缺失时服务模块自动降级为名称启发式检测。

### 2. 服务模块：`app/services/model_specs.py`
**用途**: 提供 API 供代码查询模型规格

**主要函数**:
```python
# 加载所有规格
load_specs(force_reload: bool = False) -> Dict[str, Any]

# 获取单个模型规格
get_model_spec(model_id: str) -> Optional[Dict[str, Any]]

# 检查是否支持视觉
is_vision_model(model_id: str) -> bool

# 获取 API 类型
get_api_type(model_id: str) -> Optional[str]

# 获取支持的宽高比
get_aspect_ratios(model_id: str) -> List[str]

# 获取支持的图像尺寸
get_image_sizes(model_id: str) -> List[str]

# 按类别获取模型列表
get_model_list_by_category(category: str) -> List[str]

# 强制重新加载（修改 JSON 后调用）
reload_specs()
```

**特性**:
- ✅ 自动缓存，只在首次调用时读取 JSON
- ✅ 支持精确匹配和部分匹配（如 "gpt-5" 可匹配 "gpt-5.4"）
- ✅ 优雅降级：JSON 不存在或模型未收录时，回退到原有的名称检测逻辑
- ✅ 无循环依赖（独立维护 vision_keys 列表）

### 3. 文档生成：`tools/generate_model_docs.py`
**用途**: 从 JSON 自动生成人类可读的文档

**使用**:
```bash
python tools/generate_model_docs.py
```

**输出**: `docs/MODEL_PARAMETERS.md`

### 4. 验证工具：`tools/validate_model_specs.py`
**用途**: 验证 JSON 与代码行为的一致性

**检查项**:
1. ✅ 结构验证：所有模型条目包含必需字段
2. ✅ 视觉检测一致性：JSON 中的 `supports_vision` 与代码检测结果一致
3. ✅ 宽高比完整性：nano-banana-2 有完整的 10 种宽高比

**使用**:
```bash
python tools/validate_model_specs.py
```

### 5. 测试工具：`tools/test_model_specs_service.py`
**用途**: 测试 model_specs 服务模块的所有功能

**使用**:
```bash
python tools/test_model_specs_service.py
```

## 代码集成

### 修改的文件
1. **`app/core.py:1552-1563`** - `looks_like_vision_chat_model` 函数
   - 改为优先查询 model_specs.json
   - 保留原有的名称检测作为 fallback

### 使用示例
```python
# 在代码中查询模型规格
from app.services.model_specs import is_vision_model, get_aspect_ratios, get_api_type

# 检查模型是否支持图片输入
if is_vision_model("gpt-4o"):
    # 可以发送图片
    pass

# 获取支持的宽高比
ratios = get_aspect_ratios("nano-banana-2")
# ['4:3', '3:4', '16:9', '9:16', '2:3', '3:2', '1:1', '4:5', '5:4', '21:9']

# 获取 API 类型
api_type = get_api_type("nano-banana-2")
# 'images_generations'
```

## 验证结果

### 测试结果
```
OK: All validations passed
OK: All tests passed!
OK: App integrity verified
```

### 关键验证点
- ✅ Vision 检测：旧实现和新实现结果一致（gpt-4o, gpt-4o-mini, gpt-5.4, nano-banana-2 等）
- ✅ 宽高比：CHAT_RATIO_SIZE_OPTIONS 包含完整的 10 种比例
- ✅ 模块导入：无循环依赖问题
- ✅ 缓存机制：JSON 只读取一次，后续调用使用缓存

## 新增模型流程

### 步骤
1. **编辑 JSON**: 在 `app/data/model_specs.json` 的相应类别下添加模型
   ```json
   "my-new-model": {
     "display_name": "My New Model",
     "api_type": "responses",
     "supports_vision": true,
     "parameters": {
       "stream": {"type": "boolean", "default": false}
     }
   }
   ```

2. **重新生成文档**: 
   ```bash
   python tools/generate_model_docs.py
   ```

3. **验证一致性**:
   ```bash
   python tools/validate_model_specs.py
   python tools/test_model_specs_service.py
   ```

4. **测试新功能**（可选）:
   - 如果修改了代码逻辑，运行相应的 API 测试

### 注意事项
- ✅ 无需重启应用（服务模块自动缓存，但修改 JSON 后需调用 `reload_specs()` 或重启）
- ✅ 无需修改核心代码（除非新增特殊逻辑）
- ✅ JSON 是单一数据源，修改后所有引用自动同步

## 文件清单

| 文件 | 用途 | 状态 |
|------|------|------|
| `app/data/model_specs.json` | 模型参数规范（单一数据源） | ✅ 新增 |
| `app/services/model_specs.py` | 服务模块（代码接口） | ✅ 新增 |
| `docs/MODEL_PARAMETERS.md` | 自动生成的参考文档 | ✅ 新增 |
| `tools/generate_model_docs.py` | 文档生成脚本 | ✅ 新增 |
| `tools/validate_model_specs.py` | 验证脚本 | ✅ 新增 |
| `tools/test_model_specs_service.py` | 测试脚本 | ✅ 新增 |
| `app/core.py` | 核心代码（修改 vision 检测） | ✅ 修改 |

## 总结

模型参数规范系统已成功实现，提供了：
- ✅ **单一数据源**：所有模型参数集中在 JSON 文件
- ✅ **代码接口**：服务模块提供便捷的查询函数
- ✅ **自动文档**：从 JSON 自动生成人类可读文档
- ✅ **验证机制**：确保 JSON 与代码行为一致
- ✅ **易于扩展**：新增模型只需编辑 JSON，无需修改代码

系统已验证可正常工作，可作为后续模型传参和新增模型的标准入口。
