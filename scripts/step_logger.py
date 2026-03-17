"""
步骤日志工具 — 记录每个脚本的触发时间、结束时间和报错。

日志文件：data/run.log（追加模式）

格式：
  2026-03-17 08:00:01 [START] collect_rss_papers --config ... --output ...
  2026-03-17 08:00:23 [END]   collect_rss_papers  elapsed=22s
  2026-03-17 08:00:05 [ERROR] filter_papers --filter  elapsed=4s  exit=1
  2026-03-17 08:00:05 [ERROR] filter_papers --filter  elapsed=4s  ValueError: ...

用法（在各脚本 __main__ 块中）：
  from step_logger import StepLogger
  with StepLogger("script_name args"):
      main()
"""

import time
from datetime import datetime
from pathlib import Path

_LOG = Path(__file__).parent.parent / "data" / "run.log"


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write(line: str) -> None:
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


class StepLogger:
    def __init__(self, name: str) -> None:
        self.name = name
        self._t: float = 0.0

    def __enter__(self) -> "StepLogger":
        self._t = time.time()
        _write(f"{_ts()} [START] {self.name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = int(time.time() - self._t)
        if exc_type is None:
            _write(f"{_ts()} [END]   {self.name}  elapsed={elapsed}s")
        elif exc_type is SystemExit:
            code = exc_val.code if exc_val else 0
            if code == 0 or code is None:
                _write(f"{_ts()} [END]   {self.name}  elapsed={elapsed}s")
            else:
                _write(f"{_ts()} [ERROR] {self.name}  elapsed={elapsed}s  exit={code}")
        else:
            _write(
                f"{_ts()} [ERROR] {self.name}  elapsed={elapsed}s"
                f"  {exc_type.__name__}: {exc_val}"
            )
        return False  # 不吞异常，保持原有退出行为
