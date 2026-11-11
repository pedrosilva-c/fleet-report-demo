"""Relatório diário: resumo, ocorrências por tipo, por hora e ranking por veículo.

Um dia sem eventos importados é informado como "sem dados" (cobertura
ausente), nunca como "zero ocorrências": ausência de arquivo não é ausência
de problema.
"""
from __future__ import annotations

import io
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .rules import EVENT_TYPES, RuleSet


@dataclass
class DailyReport:
    day: str
    rules_version: str
    has_data: bool
    total_events: int = 0
    occurrences: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    by_hour: list[int] = field(default_factory=lambda: [0] * 24)
    ranking: list[dict] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def build_daily_report(day: str, events, rules: RuleSet) -> DailyReport:
    report = DailyReport(day=day, rules_version=rules.version, has_data=bool(events),
                         labels={t: f"{r.label} (≥ {r.threshold:g} {r.unit})" for t, r in rules.rules.items()})
    report.by_type = {t: 0 for t in EVENT_TYPES}
    per_vehicle: dict[str, Counter] = defaultdict(Counter)
    for ev in events:
        report.total_events += 1
        if not rules.exceeds(ev["event_type"], ev["value"]):
            continue
        report.occurrences += 1
        report.by_type[ev["event_type"]] += 1
        report.by_hour[int(ev["ts"][11:13])] += 1
        per_vehicle[ev["vehicle"]][ev["event_type"]] += 1
    report.ranking = sorted(
        ({"veiculo": v, "total": sum(c.values()), **{t: c.get(t, 0) for t in EVENT_TYPES}} for v, c in per_vehicle.items()),
        key=lambda r: (-r["total"], r["veiculo"]))
    return report


FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """Neutraliza injeção de fórmula: texto vindo do arquivo importado que começa
    com = + - @ (ou tab/CR) recebe um apóstrofo, para o Excel tratá-lo como texto."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def to_xlsx(report: DailyReport) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Resumo"
    bold = Font(bold=True)
    ws.append(["Relatório diário de telemetria — DEMONSTRAÇÃO COM DADOS FICTÍCIOS"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["Dia", report.day])
    ws.append(["Versão das regras", report.rules_version])
    if not report.has_data:
        ws.append(["Situação", "Sem dados importados para este dia (cobertura ausente)"])
    else:
        ws.append(["Eventos analisados", report.total_events])
        ws.append(["Ocorrências (acima do limite)", report.occurrences])
        ws.append([])
        ws.append(["Tipo", "Ocorrências"])
        ws.cell(ws.max_row, 1).font = ws.cell(ws.max_row, 2).font = bold
        for t, n in report.by_type.items():
            ws.append([report.labels[t], n])

        rk = wb.create_sheet("Ranking por veículo")
        rk.append(["Posição", "Veículo", "Total", *[report.labels[t] for t in EVENT_TYPES]])
        for c in rk[1]:
            c.font = bold
        for i, r in enumerate(report.ranking, 1):
            rk.append([i, safe_cell(r["veiculo"]), r["total"], *[r[t] for t in EVENT_TYPES]])

        hr = wb.create_sheet("Por hora")
        hr.append(["Hora", "Ocorrências"])
        for c in hr[1]:
            c.font = bold
        for h, n in enumerate(report.by_hour):
            hr.append([f"{h:02d}:00", n])
    for sheet in wb.worksheets:
        sheet.column_dimensions["A"].width = 42
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _latin1(text: str) -> str:
    return text.replace("≥", ">=").replace("—", "-").encode("latin-1", "replace").decode("latin-1")


def to_pdf(report: DailyReport) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    nl = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _latin1("Relatório diário de telemetria"), **nl)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(150, 90, 0)
    pdf.cell(0, 6, _latin1("DEMONSTRAÇÃO — dados 100% fictícios e regras ilustrativas"), **nl)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, _latin1(f"Dia: {report.day}    Regras: versão {report.rules_version}"), **nl)
    pdf.ln(2)
    if not report.has_data:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _latin1("Sem dados importados para este dia (cobertura ausente)."), **nl)
        return bytes(pdf.output())

    pdf.cell(0, 7, _latin1(f"Eventos analisados: {report.total_events}    Ocorrências: {report.occurrences}"), **nl)
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, _latin1("Ocorrências por tipo"), **nl)
    pdf.set_font("Helvetica", "", 10)
    for t, n in report.by_type.items():
        pdf.cell(130, 6, _latin1(report.labels[t]))
        pdf.cell(0, 6, str(n), align="R", **nl)
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, _latin1("Ranking por veículo"), **nl)
    pdf.set_font("Helvetica", "B", 9)
    widths = (16, 34, 20, 40, 34, 40)
    headers = ("Pos.", "Veículo", "Total", "Velocidade", "Frenagem", "Marcha lenta")
    for w, h in zip(widths, headers):
        pdf.cell(w, 6, _latin1(h), border="B")
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for i, r in enumerate(report.ranking, 1):
        for w, v in zip(widths, (i, r["veiculo"], r["total"], r["velocidade"], r["frenagem"], r["marcha_lenta"])):
            pdf.cell(w, 6, str(v))
        pdf.ln()
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, _latin1("Ocorrências por hora"), **nl)
    pdf.set_font("Helvetica", "", 9)
    peak = max(report.by_hour) or 1
    for h, n in enumerate(report.by_hour):
        if n == 0:
            continue
        pdf.cell(16, 5, f"{h:02d}:00")
        pdf.set_fill_color(43, 138, 158)
        pdf.cell(max(1, 120 * n / peak), 5, "", fill=True)
        pdf.cell(0, 5, f"  {n}", **nl)
    return bytes(pdf.output())
