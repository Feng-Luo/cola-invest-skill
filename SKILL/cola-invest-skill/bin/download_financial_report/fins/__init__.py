# ===========================================================================
# MODIFIED FILE — Derivative Work of dayu-agent (Apache License 2.0)
# Source: https://github.com/noho/dayu-agent  (Copyright 2026 Leo Liu)
# This file was modified for the cola-invest-skill project.
# Distributed under the Apache License 2.0. See repo-root LICENSE / NOTICE.
# ===========================================================================

"""fins 子包。

仅保留下载相关子模块：ticker_normalization / _converters / domain.document_models /
pipelines.cn_download_models / downloaders。原 dayu.fins/__init__.py 会 import
tools 子包（含 FinsToolService 等重型工具），本 vendor 不携带。
"""
