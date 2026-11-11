"""Gera planilhas 100% fictícias de eventos de telemetria.

Frota, motoristas e leituras são inventados a partir de uma semente fixa, de
modo que a mesma chamada sempre produz o mesmo arquivo (útil para testes).
Algumas linhas inválidas são inseridas de propósito para demonstrar a
conferência da importação.

Uso: python -m fleetdemo.generate --dias 2 --saida amostras/
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

COLUMNS = ["data_hora", "veiculo", "motorista", "tipo_evento", "valor"]
VEHICLES = [f"VEI-{n:03d}" for n in range(1, 9)]
DRIVERS = [f"Motorista {chr(ord('A') + n)}" for n in range(8)]

# faixas plausíveis por tipo (valor medido), sem relação com qualquer operação real
RANGES = {
    "velocidade": (55.0, 110.0),     # km/h
    "frenagem": (1.0, 6.0),          # m/s²
    "marcha_lenta": (1.0, 25.0),     # minutos
}


def generate_rows(start: date, days: int = 1, events_per_day: int = 120, seed: int = 42,
                  invalid_rows: bool = True) -> list[dict[str, str]]:
    rng = random.Random(seed)
    rows: list[dict[str, str]] = []
    for d in range(days):
        day = datetime.combine(start + timedelta(days=d), datetime.min.time())
        for _ in range(events_per_day):
            event_type = rng.choice(list(RANGES))
            low, high = RANGES[event_type]
            when = day + timedelta(minutes=rng.randint(5 * 60, 22 * 60))
            idx = rng.randrange(len(VEHICLES))
            rows.append({
                "data_hora": when.strftime("%Y-%m-%d %H:%M"),
                "veiculo": VEHICLES[idx],
                "motorista": DRIVERS[(idx + rng.randint(0, 1)) % len(DRIVERS)],
                "tipo_evento": event_type,
                "valor": f"{rng.uniform(low, high):.1f}",
            })
    rows.sort(key=lambda r: (r["data_hora"], r["veiculo"]))
    if invalid_rows and rows:
        first = start.strftime("%Y-%m-%d")
        rows += [
            {"data_hora": "31/02/2026 10:00", "veiculo": "VEI-001", "motorista": "Motorista A", "tipo_evento": "velocidade", "valor": "95"},
            {"data_hora": f"{first} 11:00", "veiculo": "", "motorista": "Motorista B", "tipo_evento": "frenagem", "valor": "4.0"},
            {"data_hora": f"{first} 12:00", "veiculo": "VEI-002", "motorista": "Motorista C", "tipo_evento": "teletransporte", "valor": "1"},
            {"data_hora": f"{first} 13:00", "veiculo": "VEI-003", "motorista": "Motorista D", "tipo_evento": "velocidade", "valor": "rápido"},
            dict(rows[0]),  # duplicata exata
        ]
    return rows


def write_csv(rows: list[dict[str, str]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    return path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Gera eventos fictícios de telemetria.")
    parser.add_argument("--inicio", default="2026-01-05", help="primeiro dia (AAAA-MM-DD)")
    parser.add_argument("--dias", type=int, default=2)
    parser.add_argument("--eventos", type=int, default=120, help="eventos por dia")
    parser.add_argument("--semente", type=int, default=42)
    parser.add_argument("--saida", default="amostras")
    args = parser.parse_args(argv)
    start = date.fromisoformat(args.inicio)
    rows = generate_rows(start, args.dias, args.eventos, args.semente)
    out = write_csv(rows, Path(args.saida) / f"eventos_ficticios_{args.inicio}_{args.dias}d.csv")
    print(f"{len(rows)} linhas (5 inválidas de propósito) gravadas em {out}")


if __name__ == "__main__":
    main()
