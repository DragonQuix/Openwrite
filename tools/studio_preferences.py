"""Machine-local Studio preferences kept outside novel and framework repositories."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


MODEL_SETTING_KEYS = {
    "provider",
    "model",
    "api_format",
    "base_url",
    "context_tokens",
    "max_tokens",
    "remember_api_key",
}

MODEL_ENV_KEYS = {
    "provider": "LLM_PROVIDER",
    "model": "LLM_MODEL",
    "api_format": "LLM_API_FORMAT",
    "base_url": "LLM_BASE_URL",
    "context_tokens": "OPENWRITE_CONTEXT_TOKENS",
    "max_tokens": "LLM_MAX_TOKENS",
}


def default_studio_preferences_dir() -> Path:
    override = os.environ.get("OPENWRITE_STUDIO_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "posix" and Path("/System/Library/CoreServices").exists():
        return Path.home() / "Library" / "Application Support" / "OpenWrite"
    return Path.home() / ".config" / "openwrite"


class StudioModelSettingsStore:
    """Persist model metadata and a 0600 local credential outside all projects."""

    def __init__(self, directory: Path | None = None):
        self.directory = (directory or default_studio_preferences_dir()).resolve()
        self.settings_path = self.directory / "model-settings.json"
        self.credential_path = self.directory / ".model-api-key"

    def restore_environment(self) -> dict[str, Any]:
        settings = self.load_settings()
        for key, env_name in MODEL_ENV_KEYS.items():
            value = settings.get(key)
            if value not in {None, ""} and not os.environ.get(env_name, "").strip():
                os.environ[env_name] = str(value)
        if not os.environ.get("LLM_API_KEY", "").strip():
            credential = self.load_credential()
            if credential:
                os.environ["LLM_API_KEY"] = credential
        return settings

    def load_settings(self) -> dict[str, Any]:
        if not self.settings_path.is_file():
            return {}
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {key: value for key, value in payload.items() if key in MODEL_SETTING_KEYS}

    def save_settings(self, settings: dict[str, Any]) -> None:
        payload = {key: settings[key] for key in MODEL_SETTING_KEYS if key in settings}
        self._write_private(
            self.settings_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    def load_credential(self) -> str:
        if not self.credential_path.is_file():
            return ""
        try:
            return self.credential_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return ""

    def save_credential(self, api_key: str) -> None:
        secret = str(api_key or "").strip()
        if not secret:
            self.clear_credential()
            return
        self._write_private(self.credential_path, secret)

    def clear_credential(self) -> None:
        try:
            self.credential_path.unlink(missing_ok=True)
        except OSError:
            return

    @property
    def settings_persisted(self) -> bool:
        return self.settings_path.is_file()

    @property
    def credential_persisted(self) -> bool:
        return bool(self.load_credential())

    def _write_private(self, path: Path, content: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.directory,
            delete=False,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.chmod(0o600)
        temp_path.replace(path)
        path.chmod(0o600)
