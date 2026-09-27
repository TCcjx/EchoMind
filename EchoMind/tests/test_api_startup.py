"""启动期回归：Windows GBK 控制台上打印 BANNER 不能把服务启动打断。"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_IMPORT_AND_PRINT_BANNER = (
    "import sys; sys.path.insert(0, '.');"
    "import api.main;"
    "print(api.main.BANNER, file=sys.stdout)"
)
_IMPORT_AND_PRINT_PICKER = (
    "import sys; sys.path.insert(0, '.');"
    "import api.main;"
    "print(api.main._banner(), file=sys.stdout)"
)


def _run(code: str, io_encoding: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = io_encoding
    env.pop("PYTHONUTF8", None)   # PYTHONUTF8 会覆盖 PYTHONIOENCODING
    return subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _skip_without_fastapi():
    pytest.importorskip("fastapi", reason="api.main 依赖 FastAPI")


def test_printing_raw_banner_on_gbk_does_not_crash():
    """import 时把 stdout/stderr 降级为 errors="replace"，原始 BANNER 也不该抛异常。"""
    _skip_without_fastapi()
    result = _run(_IMPORT_AND_PRINT_BANNER, "gbk")
    assert result.returncode == 0, result.stderr
    assert "UnicodeEncodeError" not in result.stderr


def test_gbk_console_gets_readable_banner():
    """不崩但打印成一排 ? 同样是坏演示，GBK 终端要拿到 ASCII 版。"""
    _skip_without_fastapi()
    result = _run(_IMPORT_AND_PRINT_PICKER, "gbk")
    assert result.returncode == 0, result.stderr
    assert "?" not in result.stdout, result.stdout
    assert "EchoMind  v2.0" in result.stdout


def test_utf8_console_keeps_the_bear_banner():
    _skip_without_fastapi()
    result = _run(_IMPORT_AND_PRINT_PICKER, "utf-8")
    assert result.returncode == 0, result.stderr
    assert "ʕ•ᴥ•ʔ" in result.stdout


def test_api_module_imports_cleanly():
    """守卫：上面几条不是子进程压根没跑起来才“通过”的。"""
    _skip_without_fastapi()
    result = _run("import sys; sys.path.insert(0, '.'); import api.main", "gbk")
    assert result.returncode == 0, result.stderr
