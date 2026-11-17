from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import tomllib


SUPPORTED_CHANNEL_ROLES = frozenset({"vehicle_entry", "dispatch_room"})
DEFAULT_MODEL_NAME = "电子封条"
_BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULTS_FILE = _BACKEND_DIR / "configs" / "electronic_seal_defaults.toml"


def load_defaults(path: Path | str = DEFAULTS_FILE) -> dict[str, Any]:
    defaults_path = Path(path)
    with defaults_path.open("rb") as stream:
        value = tomllib.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"electronic-seal defaults must be an object: {defaults_path}")
    return value


def configured_bindings(path: Path | str = DEFAULTS_FILE) -> list[dict[str, Any]]:
    bindings = load_defaults(path).get("bindings") or {}
    if not isinstance(bindings, dict):
        raise ValueError("electronic-seal defaults bindings must be an object")
    result = []
    for channel_id, raw in bindings.items():
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").strip()
        if role not in SUPPORTED_CHANNEL_ROLES:
            raise ValueError(f"unsupported electronic-seal channel role: {role or '<empty>'}")
        result.append(
            {
                "channel_id": str(channel_id),
                "role": role,
                "bound": bool(raw.get("bound", True)),
            }
        )
    return result


def resolve_default_model_id(model_repository: Any, model_name: str = DEFAULT_MODEL_NAME) -> str:
    finder = getattr(model_repository, "find_model_by_name", None)
    if not callable(finder):
        raise ValueError("electronic-seal model repository is not available")
    model = finder(model_name)
    if model is None:
        raise ValueError(
            f"expected exactly one model named {model_name!r}, found 0"
        )
    model_id = str(model.get("model_id") or "").strip()
    if not model_id:
        raise ValueError(f"model named {model_name!r} has no model_id")
    return model_id


def role_defaults(role: str, path: Path | str = DEFAULTS_FILE) -> list[dict[str, Any]]:
    role = str(role).strip()
    if role not in SUPPORTED_CHANNEL_ROLES:
        raise ValueError(f"unsupported electronic-seal channel role: {role or '<empty>'}")
    role_data = (load_defaults(path).get("roles") or {}).get(role) or {}
    algorithm_types = role_data.get("algorithms") or []
    if not isinstance(algorithm_types, list):
        raise ValueError(f"roles.{role}.algorithms must be a list")
    result: list[dict[str, Any]] = []
    for algorithm_type in algorithm_types:
        algorithm_type = str(algorithm_type).strip()
        raw = role_data.get(algorithm_type) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"roles.{role}.{algorithm_type} must be an object")
        item: dict[str, Any] = {"algorithm_type": algorithm_type}
        for key, value in raw.items():
            if key == "algorithm_type":
                continue
            item[key] = deepcopy(value)
        if "confidence_threshold" in item:
            threshold_config = dict(item.get("threshold_config_json") or {})
            threshold_config["confidence_threshold"] = item.pop("confidence_threshold")
            item["threshold_config_json"] = threshold_config
        result.append(item)
    return result


def seed_defaults(
    store: Any,
    channel_id: str,
    role: str,
    *,
    registry_dir: Path | str,
    defaults_path: Path | str = DEFAULTS_FILE,
) -> list[dict]:
    model_name = str((load_defaults(defaults_path).get("model") or {}).get("name") or DEFAULT_MODEL_NAME)
    model_id = resolve_default_model_id(store, model_name)
    defaults = role_defaults(role, defaults_path)
    return store.ensure_default_configs(
        str(channel_id),
        defaults,
        model_id=model_id,
        preserve_existing=True,
    )


__all__ = [
    "DEFAULTS_FILE",
    "DEFAULT_MODEL_NAME",
    "SUPPORTED_CHANNEL_ROLES",
    "configured_bindings",
    "load_defaults",
    "resolve_default_model_id",
    "role_defaults",
    "seed_defaults",
]
