import threading
import tomllib
from pathlib import Path
from typing import Any

from src.core.settings import settings


class _AlgorithmRuleConfigStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False
        self._data: dict[str, dict[str, Any]] = {}

    @property
    def _config_path(self) -> Path:
        return settings.CONFIG_DIR / "algorithm_rules.toml"

    def _load(self) -> None:
        data: dict[str, dict[str, Any]] = {}
        path = self._config_path
        if path.exists():
            with path.open("rb") as fp:
                loaded = tomllib.load(fp)
            for key, value in loaded.items():
                if isinstance(value, dict):
                    data[str(key)] = dict(value)
        self._data = data
        self._loaded = True

    def get_rule_config(self, rule_type: str) -> dict[str, Any]:
        normalized = str(rule_type or "").strip().lower()
        if not normalized:
            return {}
        with self._lock:
            if not self._loaded:
                self._load()
            return dict(self._data.get(normalized) or {})


_rule_store = _AlgorithmRuleConfigStore()


def get_algorithm_rule_config(rule_type: str) -> dict[str, Any]:
    return _rule_store.get_rule_config(rule_type)
