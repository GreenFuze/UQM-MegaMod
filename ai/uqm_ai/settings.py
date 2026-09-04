"""Where the AI settings come from, in one place.

Asking a player to set environment variables before they can talk to an alien
is a bad first five minutes. Settings live in a small file next to the game's
own configuration, written by setup.ps1, and the environment still wins when
it is set so that development and CI are unaffected.

    %APPDATA%\\uqm-megamod\\uqmai.toml

Read with tomllib, which is in the standard library from 3.11 - the same
reason the character files are TOML. Nothing here executes.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

__all__ = ["Settings", "config_path"]

PROVIDERS = ("mock", "claude", "openai", "local")


def config_path() -> Path:
    """The settings file, beside the game's own cheats.cfg and saves."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "uqm-megamod" / "uqmai.toml"
    # Not Windows: follow the same convention the game uses elsewhere.
    return Path.home() / ".uqm-megamod" / "uqmai.toml"


class Settings:
    """Resolved AI settings: environment first, then the file, then defaults.

    Environment first is deliberate and the opposite of most config loaders.
    A developer exporting a variable for one run should not have to edit, and
    then remember to un-edit, a file the launcher also writes.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else config_path()
        self._file = self._read(self._path)

    @staticmethod
    def _read(path: Path) -> dict:
        """The file's contents, or nothing at all.

        A missing file is the normal case before first setup. A malformed one
        is NOT ignored: silently falling back to defaults would leave a player
        wondering why the backend they chose is not being used.
        """
        if not path.is_file():
            return {}
        try:
            with path.open("rb") as handle:
                loaded = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"{path} is not valid TOML: {exc}") from exc
        return loaded if isinstance(loaded, dict) else {}

    @property
    def path(self) -> Path:
        return self._path

    @property
    def exists(self) -> bool:
        return bool(self._file)

    def _value(self, env: str, key: str, default: str = "") -> str:
        found = os.environ.get(env)
        if found:
            return found
        found = self._file.get(key)
        return str(found) if found not in (None, "") else default

    @property
    def provider(self) -> str:
        chosen = self._value("UQMAI_PROVIDER", "provider", "claude")
        if chosen not in PROVIDERS:
            raise ValueError(
                f"provider {chosen!r} is not one of " + ", ".join(PROVIDERS)
            )
        return chosen

    @property
    def model(self) -> str:
        return self._value("UQMAI_MODEL", "model")

    @property
    def base_url(self) -> str:
        return self._value("UQMAI_BASE_URL", "base_url")

    def api_key(self, provider: str) -> str:
        """The key for this backend, whichever way it was supplied."""
        env = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
        specific = env.get(provider)
        if specific and os.environ.get(specific):
            return os.environ[specific]
        if os.environ.get("UQMAI_API_KEY"):
            return os.environ["UQMAI_API_KEY"]
        return str(self._file.get("api_key") or "")

    @property
    def use_subscription(self) -> bool:
        """Whether to let the signed-in Claude CLI answer, rather than a key.

        PERSONAL USE ONLY. Anthropic does not permit a third-party product to
        offer claude.ai login to its users, so a shipped build must not set
        this and the setup menu says so where it is offered. It exists because
        the alternative - being unable to use the tool you already pay for on
        your own machine - is what stops people trying this at all.
        """
        if os.environ.get("UQMAI_ALLOW_SUBSCRIPTION_AUTH"):
            return True
        return bool(self._file.get("use_subscription", False))

    @property
    def voice(self) -> bool:
        found = os.environ.get("UQMAI_VOICE")
        if found:
            return found.strip().lower() in ("1", "true", "yes", "on")
        return bool(self._file.get("voice", False))

    def describe(self) -> str:
        """One line for the log, so a support question answers itself."""
        where = "environment" if not self.exists else str(self._path)
        auth = "subscription" if self.use_subscription else (
            "key" if self.api_key(self.provider) else "no credentials"
        )
        return (
            f"provider={self.provider} auth={auth} "
            f"voice={'on' if self.voice else 'off'} from {where}"
        )
