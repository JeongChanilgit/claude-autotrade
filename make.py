#!/usr/bin/env python
"""Cross-platform task runner for Kairos.

Usage: python make.py <target> [args...]

Targets:
  install            Install Python deps (-e .[dev]) and web deps
  install-python     Install Python deps only
  install-web        Install web deps only
  test               Run pytest with coverage
  lint               Run ruff + mypy
  format             Run ruff format
  run                Start FastAPI dev server + Vite dev server
  run-api            Start FastAPI only
  run-web            Start Vite dev server only
  build-web          Build web production bundle
  download-data      Download Binance Vision data (Phase 1)
  oos-validate       Run final OOS validation (Phase 5)
  clean              Remove build artifacts and caches
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "kairos" / "dashboard" / "web"
VENV_DIR = ROOT / ".venv"


def venv_python() -> str:
    if os.name == "nt":
        exe = VENV_DIR / "Scripts" / "python.exe"
    else:
        exe = VENV_DIR / "bin" / "python"
    return str(exe) if exe.exists() else sys.executable


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> int:
    print(f"$ {' '.join(cmd)}" + (f"  (cwd={cwd})" if cwd else ""))
    result = subprocess.run(cmd, cwd=cwd, check=False)
    if check and result.returncode != 0:
        sys.exit(result.returncode)
    return result.returncode


def install_python() -> None:
    py = venv_python()
    run([py, "-m", "pip", "install", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "-e", ".[dev]"])


def install_web() -> None:
    if not WEB_DIR.exists():
        print(f"[skip] {WEB_DIR} not scaffolded yet.")
        return
    run(["npm", "install"], cwd=WEB_DIR)


def cmd_install(_: argparse.Namespace) -> None:
    install_python()
    install_web()


def cmd_install_python(_: argparse.Namespace) -> None:
    install_python()


def cmd_install_web(_: argparse.Namespace) -> None:
    install_web()


def cmd_test(args: argparse.Namespace) -> None:
    py = venv_python()
    extra = args.pytest_args or []
    run([py, "-m", "pytest", "--cov=kairos", "--cov-report=term-missing", *extra])


def cmd_lint(_: argparse.Namespace) -> None:
    py = venv_python()
    run([py, "-m", "ruff", "check", "kairos", "tests"])
    run([py, "-m", "mypy", "kairos"])


def cmd_format(_: argparse.Namespace) -> None:
    py = venv_python()
    run([py, "-m", "ruff", "format", "kairos", "tests"])
    run([py, "-m", "ruff", "check", "--fix", "kairos", "tests"])


def cmd_run_api(_: argparse.Namespace) -> None:
    py = venv_python()
    run([py, "-m", "uvicorn", "kairos.dashboard.api.main:app", "--reload", "--port", "8000"])


def cmd_run_web(_: argparse.Namespace) -> None:
    if not (WEB_DIR / "node_modules").exists():
        print("[info] web node_modules missing — running npm install first")
        install_web()
    run(["npm", "run", "dev"], cwd=WEB_DIR)


def cmd_run(_: argparse.Namespace) -> None:
    """Start API + Web dev servers concurrently."""
    py = venv_python()
    api = subprocess.Popen(
        [py, "-m", "uvicorn", "kairos.dashboard.api.main:app", "--reload", "--port", "8000"]
    )
    try:
        if not (WEB_DIR / "node_modules").exists():
            install_web()
        web = subprocess.Popen(["npm", "run", "dev"], cwd=WEB_DIR, shell=(os.name == "nt"))
        try:
            web.wait()
        finally:
            web.terminate()
    finally:
        api.terminate()


def cmd_build_web(_: argparse.Namespace) -> None:
    run(["npm", "run", "build"], cwd=WEB_DIR)


def cmd_download_data(args: argparse.Namespace) -> None:
    py = venv_python()
    run([py, "-m", "kairos.data.cli", "download", *args.rest])


def cmd_oos_validate(_: argparse.Namespace) -> None:
    py = venv_python()
    run([py, "-m", "kairos.backtest.oos_runner"])


def cmd_clean(_: argparse.Namespace) -> None:
    patterns = [
        "**/__pycache__",
        "**/*.pyc",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".coverage",
        "htmlcov",
        "dist",
        "build",
        "*.egg-info",
    ]
    for pat in patterns:
        for path in ROOT.glob(pat):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
    print("[clean] done")


def main() -> None:
    parser = argparse.ArgumentParser(prog="make.py")
    sub = parser.add_subparsers(dest="target", required=True)

    sub.add_parser("install").set_defaults(func=cmd_install)
    sub.add_parser("install-python").set_defaults(func=cmd_install_python)
    sub.add_parser("install-web").set_defaults(func=cmd_install_web)

    p_test = sub.add_parser("test")
    p_test.add_argument("pytest_args", nargs=argparse.REMAINDER)
    p_test.set_defaults(func=cmd_test)

    sub.add_parser("lint").set_defaults(func=cmd_lint)
    sub.add_parser("format").set_defaults(func=cmd_format)
    sub.add_parser("run").set_defaults(func=cmd_run)
    sub.add_parser("run-api").set_defaults(func=cmd_run_api)
    sub.add_parser("run-web").set_defaults(func=cmd_run_web)
    sub.add_parser("build-web").set_defaults(func=cmd_build_web)

    p_dl = sub.add_parser("download-data")
    p_dl.add_argument("rest", nargs=argparse.REMAINDER)
    p_dl.set_defaults(func=cmd_download_data)

    sub.add_parser("oos-validate").set_defaults(func=cmd_oos_validate)
    sub.add_parser("clean").set_defaults(func=cmd_clean)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
