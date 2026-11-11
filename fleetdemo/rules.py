"""Regras de classificação de eventos, carregadas de um arquivo JSON.

Os valores padrão em `config/rules.example.json` são ILUSTRATIVOS: servem para
demonstrar o mecanismo e não representam política de nenhuma empresa.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

EVENT_TYPES = ("velocidade", "frenagem", "marcha_lenta")

DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "rules.example.json"


@dataclass(frozen=True)
class Rule:
    event_type: str
    threshold: float
    unit: str
    label: str


@dataclass(frozen=True)
class RuleSet:
    version: str
    rules: dict[str, Rule]

    def exceeds(self, event_type: str, value: float) -> bool:
        """True quando o valor atinge ou ultrapassa o limite configurado."""
        rule = self.rules.get(event_type)
        return rule is not None and value >= rule.threshold


def load_rules(path: Path | str = DEFAULT_RULES_PATH) -> RuleSet:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rules: dict[str, Rule] = {}
    for event_type, spec in data["rules"].items():
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Tipo de evento desconhecido nas regras: {event_type!r}")
        threshold = float(spec["threshold"])
        if threshold <= 0:
            raise ValueError(f"Limite deve ser positivo: {event_type}")
        rules[event_type] = Rule(event_type, threshold, spec["unit"], spec["label"])
    missing = set(EVENT_TYPES) - rules.keys()
    if missing:
        raise ValueError(f"Regras ausentes: {sorted(missing)}")
    return RuleSet(version=str(data["version"]), rules=rules)
