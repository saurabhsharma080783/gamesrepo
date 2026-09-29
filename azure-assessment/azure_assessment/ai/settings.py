"""Where the language model runs and how to reach it: one settings file per installation.

Two deployment modes use the same code:

* ``local``: the model runs on the customer's own VM (the assessment appliance image). Assessment
  data never leaves the customer's environment.
* ``hosted``: the model runs on a server we operate. Only the fact sheets are sent, over HTTPS,
  with a per-customer API key.

The file is looked up in this order: ``--llm-config``, ``$AZURE_ASSESS_LLM_CONFIG``,
``/etc/azure-assess/llm.json`` (the appliance), ``~/.config/azure-assess/llm.json``. Command-line
options override it. The API key is never stored in this file: it is read from a key file
(``api_key_file``) or an environment variable (``api_key_env``).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlparse

from .llm import DEFAULT_MODEL, DEFAULT_URL, OpenAICompatibleClient

SEARCH_PATHS = (Path("/etc/azure-assess/llm.json"), Path.home() / ".config/azure-assess/llm.json")
HOSTING = ("local", "hosted", "")


class SettingsError(ValueError):
    pass


@dataclass
class LLMSettings:
    url: str = ""
    model: str = ""
    hosting: str = ""                          # local / hosted / "" (not stated)
    provider: str = ""                         # hosted mode: who operates the model server
    api_key_file: str = ""
    api_key_env: str = "AZURE_ASSESS_LLM_API_KEY"
    ca_bundle: str = ""                        # PEM file to trust, e.g. an internal CA for a local server
    timeout: float = 900
    workers: int = 1
    source: str = ""                           # file the settings came from (for messages)

    @property
    def api_key(self) -> str:
        if self.api_key_file:
            try:
                return Path(self.api_key_file).read_text(encoding="utf-8-sig").strip()
            except OSError as exc:
                raise SettingsError(f"cannot read the API key file {self.api_key_file}: {exc}") from exc
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""

    def validate(self) -> None:
        if self.hosting not in HOSTING:
            raise SettingsError(f"hosting must be 'local' or 'hosted', not '{self.hosting}'")
        u = urlparse(self.url or DEFAULT_URL)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise SettingsError(f"invalid model server URL: {self.url}")
        loopback = u.hostname in ("localhost", "127.0.0.1", "::1")
        if self.hosting == "hosted":
            if u.scheme != "https":
                raise SettingsError("hosted mode requires an https:// URL: assessment facts leave the customer's "
                                    "environment and must be encrypted in transit")
            if not self.api_key:
                raise SettingsError("hosted mode requires an API key: set api_key_file, or the environment "
                                    f"variable {self.api_key_env}")
        elif self.hosting == "local" and u.scheme == "http" and not loopback and not self.api_key:
            raise SettingsError("a local model server reached over the network needs an API key (or use https "
                                "with ca_bundle); only a server on this machine may be used without one")
        if self.ca_bundle and not Path(self.ca_bundle).is_file():
            raise SettingsError(f"ca_bundle not found: {self.ca_bundle}")

    def client(self) -> OpenAICompatibleClient:
        self.validate()
        return OpenAICompatibleClient(self.url or None, self.model or None, api_key=self.api_key or None,
                                      timeout=self.timeout, ca_bundle=self.ca_bundle or None)


def load(path: str | None = None, **overrides) -> LLMSettings:
    """Read the settings file (if any) and apply non-empty overrides from the command line."""
    candidates = [Path(path)] if path else (
        [Path(os.environ["AZURE_ASSESS_LLM_CONFIG"])] if os.environ.get("AZURE_ASSESS_LLM_CONFIG") else SEARCH_PATHS)
    data: dict = {}
    source = ""
    for p in candidates:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SettingsError(f"cannot read LLM settings {p}: {exc}") from exc
            source = str(p)
            break
        if path:
            raise SettingsError(f"LLM settings file not found: {p}")
    known = {f.name for f in fields(LLMSettings)} - {"source"}
    unknown = set(data) - known
    if unknown:
        raise SettingsError(f"unknown setting(s) in {source}: {', '.join(sorted(unknown))}")
    s = LLMSettings(**data, source=source)
    for k, v in overrides.items():
        if v not in (None, ""):
            setattr(s, k, v)
    if not s.url:
        s.url = os.environ.get("AZURE_ASSESS_LLM_URL", "")
    if not s.model:
        s.model = os.environ.get("AZURE_ASSESS_LLM_MODEL", "") or DEFAULT_MODEL
    return s
