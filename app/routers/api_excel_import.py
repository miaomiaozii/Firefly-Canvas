# -*- coding: utf-8 -*-
"""Router: Excel 导入功能。

职责：
- 接收 Excel 文件上传
- 调用 excel_importer 解析
- 调用 Plan IR 构建器生成结构化任务清单
- 返回 Plan IR（供前端预览）

API 端点：
- POST /api/excel/import-plan
  - 上传 .xlsx 文件
  - 返回 Plan IR（JSON）
"""
from fastapi import APIRouter, File, HTTPException, UploadFile
from typing import Dict, Any
import tempfile
from pathlib import Path

from excel_importer import XlsxParser
from plan_ir_builder import build_plan_ir

router = APIRouter()

# Excel 文件最大大小限制（100MB，包含内嵌图片）
MAX_EXCEL_SIZE_BYTES = 100 * 1024 * 1024


@router.post("/api/excel/import-plan")
async def import_excel_plan(file: UploadFile = File(...)) -> Dict[str, Any]:
    """从 Excel 文件导入计划。

    接收 .xlsx 文件，解析并生成结构化任务清单（Plan IR）。
    """
    # 验证文件类型
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=400,
            detail="仅支持 .xlsx 或 .xlsm 文件"
        )

    # 读取文件内容
    content = await file.read()
    if len(content) > MAX_EXCEL_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"文件超过 {MAX_EXCEL_SIZE_BYTES // 1024 // 1024}MB 限制"
        )

    # 解析 Excel
    parser = XlsxParser(content)
    try:
        parse_result = parser.parse()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Excel 解析失败，请检查文件是否为有效的 .xlsx 文件"
        )

    # 构建 Plan IR
    plan = build_plan_ir(
        source_file=file.filename,
        parse_result=parse_result
    )

    return plan.to_dict()
