"""domain 子包。

仅保留 document_models.py。原 dayu.fins.domain/__init__.py 会 import .enums 和
.tool_models（重型模型），本 vendor 不携带；外部直接走
`from download_financial_report.fins.domain.document_models import FileObjectMeta`。
"""
