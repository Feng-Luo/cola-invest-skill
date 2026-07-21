"""download_financial_report vendor 子集包。

从 dayu 项目抽离的财报下载最小闭包，仅保留 SEC / HKEXNEWS / CNINFO 三个下载器
及其直接依赖的 7 个内部模块（log / workspace_paths / contracts.env_keys /
fins._converters / fins.ticker_normalization / fins.domain.document_models /
fins.pipelines.cn_download_models）。

包入口不做任何子模块预加载，所有 import 由调用方按需显式指定。
"""
