from pathlib import Path
from types import SimpleNamespace

from tools import desktop_launcher


def test_dependency_fingerprint_changes_with_project_metadata(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pyyaml\n", encoding="utf-8")
    before = desktop_launcher.dependency_fingerprint(tmp_path)

    (tmp_path / "requirements.txt").write_text("pyyaml\npydantic\n", encoding="utf-8")

    assert desktop_launcher.dependency_fingerprint(tmp_path) != before


def test_runtime_directory_is_separate_for_macos_and_windows(tmp_path: Path):
    mac = desktop_launcher.runtime_directory(
        tmp_path, platform_name="darwin", version_info=(3, 12)
    )
    windows = desktop_launcher.runtime_directory(
        tmp_path, platform_name="win32", version_info=(3, 12)
    )

    assert mac.name == "macos-py3.12"
    assert windows.name == "windows-py3.12"
    assert desktop_launcher.runtime_python(mac, platform_name="darwin").name == "python"
    assert desktop_launcher.runtime_python(windows, platform_name="win32").name == "python.exe"


def test_select_port_reuses_openwrite_or_skips_busy_port(monkeypatch):
    monkeypatch.setattr(desktop_launcher, "_is_openwrite_server", lambda port: port == 4567)
    assert desktop_launcher.select_port(4567) == (4567, True)

    monkeypatch.setattr(desktop_launcher, "_is_openwrite_server", lambda port: False)
    monkeypatch.setattr(desktop_launcher, "_port_available", lambda port: port == 4569)
    assert desktop_launcher.select_port(4567) == (4569, False)


def test_ensure_runtime_skips_install_when_stamp_and_environment_are_healthy(
    tmp_path: Path, monkeypatch
):
    runtime = desktop_launcher.runtime_directory(tmp_path)
    python = desktop_launcher.runtime_python(runtime)
    python.parent.mkdir(parents=True)
    python.touch()
    fingerprint = desktop_launcher.dependency_fingerprint(tmp_path)
    (runtime / ".openwrite-dependencies.json").write_text(
        '{"fingerprint": "' + fingerprint + '"}\n', encoding="utf-8"
    )
    commands = []

    monkeypatch.setattr(desktop_launcher, "_installation_healthy", lambda *_: True)
    monkeypatch.setattr(desktop_launcher, "_run", lambda *args, **kwargs: commands.append(args))

    assert desktop_launcher.ensure_runtime(tmp_path) == python
    assert commands == []


def test_ensure_runtime_creates_and_installs_missing_environment(tmp_path: Path, monkeypatch):
    runtime = desktop_launcher.runtime_directory(tmp_path)
    python = desktop_launcher.runtime_python(runtime)
    commands: list[list[str]] = []

    def fake_run(command, *, cwd, check=True):
        rendered = [str(item) for item in command]
        commands.append(rendered)
        if rendered[1:3] == ["-m", "venv"]:
            python.parent.mkdir(parents=True)
            python.touch()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(desktop_launcher, "_run", fake_run)
    monkeypatch.setattr(desktop_launcher, "_installation_healthy", lambda *_: True)

    assert desktop_launcher.ensure_runtime(tmp_path) == python
    assert commands[0][1:3] == ["-m", "venv"]
    assert commands[1][1:4] == ["-m", "pip", "install"]
    assert commands[2][1:4] == ["-m", "pip", "install"]
    assert "--editable" in commands[2]
    assert (runtime / ".openwrite-dependencies.json").is_file()


def test_double_click_launchers_are_present_and_use_shared_bootstrap():
    root = Path(__file__).parent.parent
    mac = (root / "启动 OpenWrite.command").read_text(encoding="utf-8")
    windows = (root / "启动 OpenWrite.bat").read_text(encoding="utf-8")

    assert "tools/desktop_launcher.py" in mac
    assert "tools\\desktop_launcher.py" in windows
    assert "Python 3.10" in mac
    assert "Python 3.10" in windows
    assert '"%~1" -c' in windows
    assert "launch_status=$?" in mac
    assert " -u " in mac
    assert " -u " in windows
    assert "-m tools.desktop_launcher" in mac
    assert "-m tools.desktop_launcher" in windows


def test_installed_launcher_reuses_the_active_environment(monkeypatch) -> None:
    health_checks = []
    monkeypatch.setattr(
        desktop_launcher,
        "_installation_healthy",
        lambda *args, **kwargs: health_checks.append((args, kwargs)) or True,
    )

    assert desktop_launcher.ensure_runtime(Path(desktop_launcher.sys.prefix)) == Path(
        desktop_launcher.sys.executable
    ).absolute()
    assert health_checks[0][1] == {"check_dependencies": False}


def test_installation_health_can_skip_pip_check(tmp_path: Path, monkeypatch) -> None:
    python = tmp_path / "python"
    python.touch()
    commands = []
    monkeypatch.setattr(
        desktop_launcher,
        "_run",
        lambda command, **_: commands.append([str(item) for item in command]),
    )

    assert desktop_launcher._installation_healthy(
        python, tmp_path, check_dependencies=False
    )
    assert len(commands) == 1
    assert commands[0][1] == "-c"
