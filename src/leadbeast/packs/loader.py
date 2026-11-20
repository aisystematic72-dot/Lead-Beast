"""Load and validate packs from YAML directories."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from leadbeast.packs.schema import Offer, PackSpec


class PackValidationError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def _read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise PackValidationError([f"{path}: top level must be a mapping"])
    return data


def load_pack(directory: Path) -> PackSpec:
    directory = Path(directory)
    data = _read(directory / "pack.yaml")
    problems_file = directory / "problems.yaml"
    if problems_file.exists():
        extra = _read(problems_file)
        data.setdefault("problems", extra.get("problems", []))
        if "pressure" in extra:
            data.setdefault("pressure", extra["pressure"])
    tdir = directory / "templates"
    if tdir.is_dir():
        templates = dict(data.get("templates") or {})
        for f in sorted(tdir.glob("*.txt")):
            templates.setdefault(f.stem, f.read_text(encoding="utf-8"))
        data["templates"] = templates
    return PackSpec.model_validate(data)


def validate_pack(
    pack: PackSpec,
    known_detector_signals: dict[str, tuple[str, ...]] | None = None,
    golden_labelled: set[str] | None = None,
) -> list[str]:
    """Cross-checks that Pydantic alone cannot do. Returns a list of problems (empty = ok)."""
    problems: list[str] = []
    if known_detector_signals is not None:
        unknown = [d for d in pack.detectors if d not in known_detector_signals]
        if unknown:
            problems.append(f"unknown detectors enabled: {unknown}")
        produced = {s for d in pack.detectors for s in known_detector_signals.get(d, ())}
        for p in pack.problems:
            for sym in p.symptoms:
                if sym.signal not in produced:
                    problems.append(
                        f"problem {p.id}: symptom {sym.signal} not produced by an enabled detector"
                    )
    if golden_labelled is not None:
        for p in pack.problems:
            if p.use_in_message and any(s.signal not in golden_labelled for s in p.symptoms):
                problems.append(
                    f"problem {p.id}: use_in_message needs golden labels for every symptom"
                )
    for name, part in pack.parts.items():
        seen: set[str] = set()
        for s in part.signals:
            if s.signal in seen:
                problems.append(f"part {name}: duplicate signal {s.signal}")
            seen.add(s.signal)
    return problems


def load_offer(path: Path) -> Offer:
    return Offer.model_validate(_read(path))


def list_packs(root: Path) -> list[Path]:
    return sorted(p.parent for p in Path(root).glob("*/pack.yaml"))
