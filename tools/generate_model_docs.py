"""Generate MODEL_PARAMETERS.md documentation from model_specs.json.

Run this script after updating model_specs.json to keep documentation in sync:
    python tools/generate_model_docs.py
"""
import json
import os
from datetime import datetime

# Paths
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPECS_PATH = os.path.join(ROOT, 'app', 'data', 'model_specs.json')
OUTPUT_PATH = os.path.join(ROOT, 'docs', 'MODEL_PARAMETERS.md')


def load_specs():
    """Load model specs from JSON file."""
    with open(SPECS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def generate_markdown(specs):
    """Generate Markdown documentation from specs."""
    lines = []
    
    # Header
    lines.append("# 模型参数规范 (MODEL_PARAMETERS)")
    lines.append("")
    lines.append("> 本文档从 `app/data/model_specs.json` 自动生成，请勿手动编辑。")
    lines.append(f"> 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("## 概述")
    lines.append("")
    lines.append("本系统支持三类模型：")
    lines.append("")
    lines.append("| 类别 | 用途 | API 类型 |")
    lines.append("|------|------|----------|")
    lines.append("| 对话模型 | 文本对话、图片分析 | responses / chat_completions |")
    lines.append("| 图像模型 | 图像生成、图像编辑 | images_generations |")
    lines.append("| 视频模型 | 视频生成 | v2_generations |")
    lines.append("")
    
    # Chat models
    lines.append("---")
    lines.append("")
    lines.append("## 对话模型 (Chat Models)")
    lines.append("")
    lines.append("| 模型 ID | 显示名称 | API 类型 | 支持视觉 |")
    lines.append("|---------|----------|----------|----------|")
    
    chat_models = specs.get('chat_models', {})
    for model_id, spec in chat_models.items():
        display_name = spec.get('display_name', model_id)
        api_type = spec.get('api_type', 'unknown')
        supports_vision = "✅" if spec.get('supports_vision') else "❌"
        lines.append(f"| {model_id} | {display_name} | {api_type} | {supports_vision} |")
    
    # Chat model details
    lines.append("")
    lines.append("### 对话模型参数详情")
    lines.append("")
    for model_id, spec in chat_models.items():
        lines.append(f"#### {spec.get('display_name', model_id)}")
        lines.append(f"- **模型 ID**: `{model_id}`")
        lines.append(f"- **API 类型**: {spec.get('api_type', 'unknown')}")
        lines.append(f"- **支持视觉**: {'是' if spec.get('supports_vision') else '否'}")
        
        params = spec.get('parameters', {})
        if params:
            lines.append("- **参数**:")
            for param_name, param_spec in params.items():
                param_type = param_spec.get('type', 'unknown')
                default = param_spec.get('default', 'N/A')
                lines.append(f"  - `{param_name}`: {param_type} (默认: {default})")
        
        notes = spec.get('notes')
        if notes:
            lines.append(f"- **备注**: {notes}")
        lines.append("")
    
    # Image models
    lines.append("---")
    lines.append("")
    lines.append("## 图像模型 (Image Models)")
    lines.append("")
    lines.append("| 模型 ID | 显示名称 | API 类型 | 支持视觉 | 异步模式 |")
    lines.append("|---------|----------|----------|----------|----------|")
    
    image_models = specs.get('image_models', {})
    for model_id, spec in image_models.items():
        display_name = spec.get('display_name', model_id)
        api_type = spec.get('api_type', 'unknown')
        supports_vision = "✅" if spec.get('supports_vision') else "❌"
        async_mode = "✅" if spec.get('async_mode') else "❌"
        lines.append(f"| {model_id} | {display_name} | {api_type} | {supports_vision} | {async_mode} |")
    
    # Image model details
    lines.append("")
    lines.append("### 图像模型参数详情")
    lines.append("")
    for model_id, spec in image_models.items():
        lines.append(f"#### {spec.get('display_name', model_id)}")
        lines.append(f"- **模型 ID**: `{model_id}`")
        lines.append(f"- **API 类型**: {spec.get('api_type', 'unknown')}")
        lines.append(f"- **支持视觉**: {'是' if spec.get('supports_vision') else '否'}")
        lines.append(f"- **异步模式**: {'是' if spec.get('async_mode') else '否'}")
        
        params = spec.get('parameters', {})
        if params:
            lines.append("- **参数**:")
            for param_name, param_spec in params.items():
                param_type = param_spec.get('type', 'unknown')
                default = param_spec.get('default', 'N/A')
                
                if param_type == 'enum':
                    options = param_spec.get('options', [])
                    lines.append(f"  - `{param_name}`: 枚举类型 (默认: {default})")
                    lines.append(f"    - 可选值: {', '.join(options)}")
                else:
                    lines.append(f"  - `{param_name}`: {param_type} (默认: {default})")
        
        notes = spec.get('notes')
        if notes:
            lines.append(f"- **备注**: {notes}")
        lines.append("")
    
    # Video models
    lines.append("---")
    lines.append("")
    lines.append("## 视频模型 (Video Models)")
    lines.append("")
    lines.append("| 模型 ID | 显示名称 | API 类型 | 支持视觉 | 异步模式 |")
    lines.append("|---------|----------|----------|----------|----------|")
    
    video_models = specs.get('video_models', {})
    for model_id, spec in video_models.items():
        display_name = spec.get('display_name', model_id)
        api_type = spec.get('api_type', 'unknown')
        supports_vision = "✅" if spec.get('supports_vision') else "❌"
        async_mode = "✅" if spec.get('async_mode') else "❌"
        lines.append(f"| {model_id} | {display_name} | {api_type} | {supports_vision} | {async_mode} |")
    
    # Video model details
    lines.append("")
    lines.append("### 视频模型参数详情")
    lines.append("")
    for model_id, spec in video_models.items():
        lines.append(f"#### {spec.get('display_name', model_id)}")
        lines.append(f"- **模型 ID**: `{model_id}`")
        lines.append(f"- **API 类型**: {spec.get('api_type', 'unknown')}")
        lines.append(f"- **支持视觉**: {'是' if spec.get('supports_vision') else '否'}")
        lines.append(f"- **异步模式**: {'是' if spec.get('async_mode') else '否'}")
        
        params = spec.get('parameters', {})
        if params:
            lines.append("- **参数**:")
            for param_name, param_spec in params.items():
                param_type = param_spec.get('type', 'unknown')
                default = param_spec.get('default', 'N/A')
                
                if param_type == 'enum':
                    options = param_spec.get('options', [])
                    lines.append(f"  - `{param_name}`: 枚举类型 (默认: {default})")
                    lines.append(f"    - 可选值: {', '.join(options)}")
                elif param_type == 'integer':
                    range_spec = param_spec.get('range', [])
                    unit = param_spec.get('unit', '')
                    range_str = f"范围: {range_spec[0]}-{range_spec[1]}" if range_spec else ""
                    unit_str = f"，单位: {unit}" if unit else ""
                    lines.append(f"  - `{param_name}`: 整数类型 (默认: {default}，{range_str}{unit_str})")
                else:
                    lines.append(f"  - `{param_name}`: {param_type} (默认: {default})")
        
        notes = spec.get('notes')
        if notes:
            lines.append(f"- **备注**: {notes}")
        lines.append("")
    
    # Footer
    lines.append("---")
    lines.append("")
    lines.append("## 使用说明")
    lines.append("")
    lines.append("### 新增模型")
    lines.append("1. 编辑 `app/data/model_specs.json`，在相应类别下添加模型条目")
    lines.append("2. 运行 `python tools/generate_model_docs.py` 重新生成本文档")
    lines.append("3. 如需代码引用，使用 `app.services.model_specs` 模块")
    lines.append("")
    lines.append("### 代码引用示例")
    lines.append("```python")
    lines.append("from app.services.model_specs import get_model_spec, is_vision_model, get_aspect_ratios")
    lines.append("")
    lines.append("# 获取模型规格")
    lines.append("spec = get_model_spec('nano-banana-2')")
    lines.append("")
    lines.append("# 检查是否支持视觉")
    lines.append("if is_vision_model('gpt-4o'):")
    lines.append("    # 可以发送图片")
    lines.append("    pass")
    lines.append("")
    lines.append("# 获取支持的宽高比")
    lines.append("ratios = get_aspect_ratios('nano-banana-2')")
    lines.append("```")
    lines.append("")
    
    return '\n'.join(lines)


def main():
    """Generate the documentation file."""
    print("Loading model specs...")
    specs = load_specs()
    
    print("Generating Markdown documentation...")
    content = generate_markdown(specs)
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    
    # Write to file
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"Documentation generated: {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
