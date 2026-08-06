"""PyInstaller 打包入口：python -m aion_agent 的等效启动点

用法（在项目根目录执行）：
    pyinstaller --onefile --name aion packaging/launcher.py
"""

from aion_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
