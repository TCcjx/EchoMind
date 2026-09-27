"""让 `pytest tests/` 在项目根目录直接可运行：把项目根加入 sys.path。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
