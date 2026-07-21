# ===========================================================================
# MODIFIED FILE — Derivative Work of dayu-agent (Apache License 2.0)
# Source: https://github.com/noho/dayu-agent  (Copyright 2026 Leo Liu)
# This file was modified for the cola-invest-skill project.
# Distributed under the Apache License 2.0. See repo-root LICENSE / NOTICE.
# ===========================================================================

"""pipelines 子包。

仅保留 cn_download_models.py（typed model 定义，零依赖）。原 dayu.fins.pipelines/__init__.py
会 import .base / .cn_pipeline / .sec_pipeline / .factory 四个重型 pipeline
模块（含 storage / docling 调用），本 vendor 不携带；外部直接走
`from download_financial_report.fins.pipelines.cn_download_models import CnReportCandidate`。
"""
