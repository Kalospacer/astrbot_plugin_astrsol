"""影子模式台账：每次工具调用追加一行 JSONL。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from astrbot.api import logger


def append(path: Path, record: dict) -> None:
    """把一条记录追加到台账文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {"ts": datetime.now(timezone.utc).isoformat(), **record},
        ensure_ascii=False,
    )
    with path.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")
    logger.info("SoL-Astr ledger: %s", line)
