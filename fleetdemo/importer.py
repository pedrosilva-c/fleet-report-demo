"""Leitura e conferência de planilhas de eventos (CSV ou XLSX).

Cada linha é aceita ou rejeitada com um motivo legível. Nada é corrigido
silenciosamente: uma data inválida, um tipo desconhecido ou uma duplicata vira
uma rejeição listada no relatório de conferência.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime

from .rules import EVENT_TYPES

REQUIRED_COLUMNS = ("data_hora", "veiculo", "motorista", "tipo_evento", "valor")
DATETIME_FORMAT = "%Y-%m-%d %H:%M"


@dataclass(frozen=True)
class Event:
    timestamp: datetime
    vehicle: str
    driver: str
    event_type: str
    value: float

    @property
    def key(self) -> tuple:
        return (self.timestamp.isoformat(timespec="minutes"), self.vehicle, self.event_type, self.value)


@dataclass
class Rejection:
    line: int
    reason: str
    raw: dict


@dataclass
class ParseResult:
    accepted: list[Event] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.accepted) + len(self.rejected)


class FormatError(ValueError):
    """Arquivo ilegível ou sem as colunas obrigatórias."""


def _read_table(filename: str, content: bytes) -> list[dict]:
    name = filename.lower()
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:  # arquivo corrompido ou não-XLSX
            raise FormatError(f"XLSX ilegível: {exc}") from exc
        rows = list(wb.worksheets[0].iter_rows(values_only=True))
        wb.close()
        if not rows:
            raise FormatError("Planilha vazia.")
        header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]
        out = []
        for r in rows[1:]:
            values = ["" if v is None else (v.strftime(DATETIME_FORMAT) if isinstance(v, datetime) else str(v)) for v in r]
            out.append(dict(zip(header, values)))
        return out
    if name.endswith(".csv"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        sample = text[:2048]
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if reader.fieldnames is None:
            raise FormatError("CSV vazio.")
        reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
        return list(reader)
    raise FormatError("Formato não suportado: envie .csv ou .xlsx.")


def parse_events(filename: str, content: bytes) -> ParseResult:
    table = _read_table(filename, content)
    if table:
        missing = [c for c in REQUIRED_COLUMNS if c not in table[0]]
        if missing:
            raise FormatError(f"Colunas obrigatórias ausentes: {', '.join(missing)}")
    result = ParseResult()
    seen: set[tuple] = set()
    for line, raw in enumerate(table, start=2):  # linha 1 é o cabeçalho
        if not any((v or "").strip() for v in raw.values()):
            continue  # linha totalmente vazia
        reason, event = _validate(raw)
        if event is not None and event.key in seen:
            reason, event = "Linha duplicada no arquivo.", None
        if event is None:
            result.rejected.append(Rejection(line, reason, raw))
        else:
            seen.add(event.key)
            result.accepted.append(event)
    return result


def _validate(raw: dict) -> tuple[str, Event | None]:
    get = lambda k: (raw.get(k) or "").strip()
    try:
        ts = datetime.strptime(get("data_hora"), DATETIME_FORMAT)
    except ValueError:
        return f"Data/hora inválida: {get('data_hora')!r} (esperado AAAA-MM-DD HH:MM).", None
    vehicle = get("veiculo")
    if not vehicle:
        return "Veículo não informado.", None
    event_type = get("tipo_evento").lower()
    if event_type not in EVENT_TYPES:
        return f"Tipo de evento desconhecido: {event_type!r}.", None
    try:
        value = float(get("valor").replace(",", "."))
    except ValueError:
        return f"Valor não numérico: {get('valor')!r}.", None
    if value < 0:
        return "Valor negativo.", None
    return "", Event(ts, vehicle, get("motorista") or "(não informado)", event_type, value)
