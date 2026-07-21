"""cola_fetch 工作区状态路径。

仅保留 SEC 限流共享状态目录构造逻辑，由 sec_downloader 使用。
原 dayu.workspace_paths 包含 host SQLite / conversation / wechat 等
无关路径常量，本 vendor 不携带。

状态目录约定：``<workspace_root>/.cola_fetch/sec_throttle/``
"""

from __future__ import annotations

from pathlib import Path


_COLA_FETCH_INTERNAL_ROOT_RELATIVE_DIR = Path(".cola_fetch")
"""cola_fetch 在工作区下的隐藏根目录（与原 dayu 的 ``.dayu/`` 隔离）。"""

SEC_THROTTLE_RELATIVE_DIR = _COLA_FETCH_INTERNAL_ROOT_RELATIVE_DIR / "sec_throttle"
"""SEC 全局限流共享状态子目录（跨进程共享节流状态）。"""


def build_sec_throttle_dir(workspace_root: Path) -> Path:
    """构造 SEC 全局限流状态默认目录。

    Args:
        workspace_root: 工作区根目录。

    Returns:
        工作区下 ``.cola_fetch/sec_throttle`` 目录路径。

    Raises:
        无。
    """

    return workspace_root / SEC_THROTTLE_RELATIVE_DIR


__all__ = [
    "SEC_THROTTLE_RELATIVE_DIR",
    "build_sec_throttle_dir",
]
