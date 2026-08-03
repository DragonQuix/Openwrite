"""Cross-platform dependency bootstrap and one-click Studio launcher."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Sequence
from pathlib import Path

MIN_PYTHON = (3, 10)
DEFAULT_PORT = 4567
PORT_SCAN_LIMIT = 20
LAUNCHER_VERSION = 1
REQUIRED_IMPORTS = (
    "pydantic",
    "yaml",
    "openai",
    "anthropic",
    "markdown_it",
    "prompt_toolkit",
)


class LauncherError(RuntimeError):
    pass


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def runtime_directory(
    root: Path,
    *,
    platform_name: str | None = None,
    version_info: tuple[int, int] | None = None,
) -> Path:
    platform_name = platform_name or sys.platform
    version_info = version_info or (sys.version_info.major, sys.version_info.minor)
    if platform_name == "darwin":
        system = "macos"
    elif platform_name.startswith("win"):
        system = "windows"
    else:
        system = "unix"
    return root / ".openwrite-runtime" / f"{system}-py{version_info[0]}.{version_info[1]}"


def runtime_python(runtime: Path, *, platform_name: str | None = None) -> Path:
    platform_name = platform_name or sys.platform
    if platform_name.startswith("win"):
        return runtime / "Scripts" / "python.exe"
    return runtime / "bin" / "python"


def dependency_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(f"launcher:{LAUNCHER_VERSION}\n".encode())
    digest.update(f"python:{sys.version_info.major}.{sys.version_info.minor}\n".encode())
    digest.update(f"platform:{sys.platform}\n".encode())
    for name in ("pyproject.toml", "requirements.txt"):
        path = root / name
        digest.update(f"file:{name}\n".encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _run(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [os.fspath(item) for item in command],
        cwd=cwd,
        check=check,
        text=True,
        env={**os.environ, "PYTHONUTF8": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
    )


def _installation_healthy(python: Path, root: Path) -> bool:
    if not python.is_file():
        return False
    imports = ", ".join(REQUIRED_IMPORTS)
    try:
        _run(
            [python, "-c", f"import {imports}; import tools.cli"],
            cwd=root,
        )
        _run([python, "-m", "pip", "check"], cwd=root)
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def ensure_runtime(root: Path) -> Path:
    runtime = runtime_directory(root)
    python = runtime_python(runtime)
    stamp_path = runtime / ".openwrite-dependencies.json"
    fingerprint = dependency_fingerprint(root)
    stamp = _read_stamp(stamp_path)
    if stamp.get("fingerprint") == fingerprint and _installation_healthy(python, root):
        print("[OpenWrite] 运行环境与依赖已就绪。")
        return python

    if not python.is_file():
        print(f"[OpenWrite] 正在创建独立运行环境：{runtime}")
        runtime.parent.mkdir(parents=True, exist_ok=True)
        try:
            _run([sys.executable, "-m", "venv", runtime], cwd=root)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise LauncherError("无法创建 Python 虚拟环境，请确认当前 Python 包含 venv。") from exc

    print("[OpenWrite] 正在检查并下载所需依赖，首次运行可能需要几分钟……")
    try:
        _run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "pip>=23",
                "setuptools",
                "wheel",
            ],
            cwd=root,
        )
        _run([python, "-m", "pip", "install", "--editable", root], cwd=root)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LauncherError(
            "依赖下载或安装失败。请检查网络、代理和磁盘空间后重新双击启动。"
        ) from exc
    if not _installation_healthy(python, root):
        raise LauncherError("依赖安装完成但环境自检未通过，请重新运行启动器。")
    stamp_path.write_text(
        json.dumps(
            {
                "fingerprint": fingerprint,
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("[OpenWrite] 依赖安装与自检完成。")
    return python


def _read_stamp(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_openwrite_server(port: int) -> bool:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/health")
    try:
        with urllib.request.urlopen(request, timeout=0.35) as response:
            server = str(response.headers.get("Server") or "")
            payload = json.loads(response.read().decode("utf-8"))
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ):
        return False
    return server.startswith("OpenWriteStudio/") and payload == {"ok": True}


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def select_port(preferred: int) -> tuple[int, bool]:
    if _is_openwrite_server(preferred):
        return preferred, True
    for port in range(preferred, preferred + PORT_SCAN_LIMIT):
        if _port_available(port):
            return port, False
    raise LauncherError(
        f"端口 {preferred}-{preferred + PORT_SCAN_LIMIT - 1} 均被占用，请关闭相关程序后重试。"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenWrite 一键启动器")
    parser.add_argument("--check-only", action="store_true", help="只检查环境，不启动 Studio")
    parser.add_argument("--debug", action="store_true", help="启用 Studio debug 日志")
    parser.add_argument("--project", help="直接打开指定作品目录")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="首选端口")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if sys.version_info < MIN_PYTHON:
        print("[OpenWrite] 需要 Python 3.10 或更高版本。")
        return 2
    root = repository_root()
    try:
        python = ensure_runtime(root)
        if args.check_only:
            print(f"[OpenWrite] 环境检查通过：{python}")
            return 0
        port, already_running = select_port(args.port)
        url = f"http://127.0.0.1:{port}/"
        if already_running:
            print(f"[OpenWrite] Studio 已在运行：{url}")
            webbrowser.open(url)
            return 0
        command: list[str | os.PathLike[str]] = [
            python,
            "-m",
            "tools.cli",
            "studio",
            "--port",
            str(port),
        ]
        if args.project:
            command.extend(["--project", str(Path(args.project).expanduser())])
        if args.debug:
            command.append("--debug")
        print(f"[OpenWrite] 正在启动 Studio：{url}")
        return _run(command, cwd=root, check=False).returncode
    except LauncherError as exc:
        print(f"\n[OpenWrite] 启动失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
