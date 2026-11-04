"""Disclosed, reusable AI-written software guidance, separate from business facts."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.jiahui.import_knowledge import import_knowledge

GUIDE_VERSION = "软件操作指引-v1.1.1"
GUIDE_TITLE = "【AI整理】嘉惠助手操作指引（非公司制度）"
GUIDE_TEXT = """# 【AI整理】嘉惠助手操作指引（非公司制度）

来源说明：本指南由AI根据软件实际功能整理，仅供使用软件和资料核实参考，尚未由嘉惠确认为公司业务制度。它不是产品资料，不包含食品功效、保质期、售价、库存或替代适用性结论。

## 导入和检查资料
管理员在“资料与模型设置 → 资料管理”选择文字版PDF或UTF-8产品CSV。
PDF会按页保留原文；能够明确识别的标准六列表格会生成可查询参考产品目录，保留前导零、重复商品编号、规格原文及PDF页码。参考目录未确认现行有效，不能当作最新成交价或公司制度。
导入后核对“可查询产品目录”条数、“已接入知识资料”页数，以及已导入来源列表和导入诊断。扫描页或无法识别的表格会明确提示，需要OCR核对或人工整理，不能用模型补齐缺失字段。
如旧软件已保留PDF但没有产品记录，可以点击“检查并修复已导入目录”，复用原件补建目录；不要删除账号或原始资料。
CSV草稿不进入正常查询，业务核实后可以重新选择同一CSV并按现行资料导入。原文缺少的品牌、规格、储温、保质期和换算应留空。

## 查询和核实步骤
先输入完整商品编号、名称、品牌或规格；同名多规格、重复编号应先选择具体记录，不能默认第一条。
可以问“有哪些资料”查看当前账号已授权的目录条数、原文页数与来源版本。
对比产品时检查各自引用；资料缺失或冲突时明确需要业务确认，不根据品类猜测。
数量换算只使用已确认换算和程序计算。当前管家婆未接入，价格、库存和订单查询应显示“尚未接入业务数据”。

## 保存、重启和升级
导入后的账号、产品目录、参考原文及备份保存在本机用户数据目录，与软件解压目录分开。关闭后重新打开可以继续使用，不必每次导入。
重复导入相同PDF或相同内部记录ID与版本不会重复增加记录；更新前自动备份。升级时先关闭旧软件，再打开新版；不要清空原有用户资料目录。
资料只向授权岗位展示；Key不属于RAG资料，不应上传到知识库或发送到聊天中。连接DeepSeek时，问题及相关授权资料片段会发送至其API以选择依据。
"""


def install_usage_guide(data_dir: str | Path) -> dict:
    root = Path(data_dir) / "sources" / "software-guides"
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(GUIDE_TEXT.encode()).hexdigest()
    source = root / (digest + ".md")
    if not source.exists():
        source.write_text(GUIDE_TEXT, encoding="utf-8")
    return import_knowledge(
        source,
        data_dir,
        document_id="jiahui-software-usage-guide",
        version=GUIDE_VERSION,
        location="使用软件与资料核实步骤",
        status="reference",
        scope="knowledge",
        title=GUIDE_TITLE,
        source_file=GUIDE_TITLE + ".md",
    )
