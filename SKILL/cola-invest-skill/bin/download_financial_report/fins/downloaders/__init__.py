# ===========================================================================
# MODIFIED FILE — Derivative Work of dayu-agent (Apache License 2.0)
# Source: https://github.com/noho/dayu-agent  (Copyright 2026 Leo Liu)
# This file was modified for the cola-invest-skill project.
# Distributed under the Apache License 2.0. See repo-root LICENSE / NOTICE.
# ===========================================================================

"""下载器子包。

仅导出三个 downloader 类，无重型依赖。
"""

from .cninfo_downloader import CninfoDiscoveryClient
from .hkexnews_downloader import HkexnewsDiscoveryClient
from .sec_downloader import SecDownloader

__all__ = ["CninfoDiscoveryClient", "HkexnewsDiscoveryClient", "SecDownloader"]
