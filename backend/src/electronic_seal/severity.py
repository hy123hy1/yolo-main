from __future__ import annotations

from pathlib import Path
import tomllib


_BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SEVERITY_FILE = _BACKEND_DIR / "configs" / "electronic_seal_severity.toml"
ALLOWED_SEVERITIES = frozenset({"red", "orange", "yellow"})
NO_SEVERITY = "none"


def load_event_severities(path: Path | str = DEFAULT_SEVERITY_FILE) -> dict[str, str]:
    with Path(path).open("rb") as stream:
        payload = tomllib.load(stream)
    events = payload.get("events") or {}
    if not isinstance(events, dict):
        raise ValueError("electronic-seal severity events must be an object")
    return {str(key).strip(): str(value).strip().lower() for key, value in events.items()}


def event_severity(
    algorithm_type: str,
    path: Path | str = DEFAULT_SEVERITY_FILE,
) -> str | None:
    normalized_algorithm = str(algorithm_type or "").strip()
    severity = load_event_severities(path).get(normalized_algorithm)
    if severity is None:
        raise ValueError(
            f"unsupported electronic-seal event algorithm: {normalized_algorithm or '<empty>'}"
        )
    if severity == NO_SEVERITY:
        return None
    if severity not in ALLOWED_SEVERITIES:
        raise ValueError("electronic-seal severity must be red, orange, yellow, or none")
    return severity


__all__ = [
    "ALLOWED_SEVERITIES",
    "DEFAULT_SEVERITY_FILE",
    "NO_SEVERITY",
    "event_severity",
    "load_event_severities",
]
