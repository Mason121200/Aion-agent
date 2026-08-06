"""Aion Agent 演示入口

用法：
    python examples/demo.py
等价于：
    python -m aion_agent demo
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aion_agent.cli import run_demo  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_demo())