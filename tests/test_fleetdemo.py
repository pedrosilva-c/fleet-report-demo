import io
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from fleetdemo.api import create_app
from fleetdemo.generate import COLUMNS, generate_rows, write_csv
from fleetdemo.importer import FormatError, parse_events
from fleetdemo.reports import build_daily_report
from fleetdemo.rules import load_rules
from fleetdemo.storage import DuplicateFileError, Store

DAY = date(2026, 1, 5)


def csv_bytes(rows, delimiter=";"):
    lines = [delimiter.join(COLUMNS)] + [delimiter.join(str(r[c]) for c in COLUMNS) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


@pytest.fixture
def rules():
    return load_rules()


# ---------- gerador ----------

def test_generator_is_deterministic():
    assert generate_rows(DAY, 1, 50, seed=7) == generate_rows(DAY, 1, 50, seed=7)
    assert generate_rows(DAY, 1, 50, seed=7) != generate_rows(DAY, 1, 50, seed=8)


def test_generator_uses_only_fictitious_identifiers():
    rows = generate_rows(DAY, 2, 80, invalid_rows=False)
    assert all(r["veiculo"].startswith("VEI-") for r in rows)
    assert all(r["motorista"].startswith("Motorista ") for r in rows)


# ---------- regras ----------

def test_rules_threshold_is_inclusive(rules):
    limit = rules.rules["velocidade"].threshold
    assert rules.exceeds("velocidade", limit)
    assert not rules.exceeds("velocidade", limit - 0.1)


def test_rules_reject_unknown_type_and_missing(tmp_path):
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps({"version": "x", "rules": {"voo": {"threshold": 1, "unit": "m", "label": "?"}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_rules(bad)
    bad.write_text(json.dumps({"version": "x", "rules": {"velocidade": {"threshold": 1, "unit": "km/h", "label": "v"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="ausentes"):
        load_rules(bad)


# ---------- importação ----------

def test_parse_rejects_each_invalid_row_with_reason():
    rows = generate_rows(DAY, 1, 30, seed=1)  # inclui 5 linhas inválidas de propósito
    result = parse_events("e.csv", csv_bytes(rows))
    assert len(result.accepted) == 30
    reasons = " | ".join(r.reason for r in result.rejected)
    assert len(result.rejected) == 5
    for fragment in ("Data/hora inválida", "Veículo não informado", "Tipo de evento desconhecido", "Valor não numérico", "duplicada"):
        assert fragment in reasons


def test_parse_accepts_comma_delimiter_and_decimal_comma():
    data = "data_hora,veiculo,motorista,tipo_evento,valor\n2026-01-05 08:00,VEI-001,Motorista A,frenagem,\"4,2\"\n".encode()
    result = parse_events("e.csv", data)
    assert result.accepted[0].value == pytest.approx(4.2)


def test_parse_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.append(list(COLUMNS))
    ws.append(["2026-01-05 09:30", "VEI-002", "Motorista B", "velocidade", 101])
    buf = io.BytesIO()
    wb.save(buf)
    result = parse_events("e.xlsx", buf.getvalue())
    assert [(e.vehicle, e.value) for e in result.accepted] == [("VEI-002", 101.0)]


def test_parse_missing_columns_and_unsupported_format():
    with pytest.raises(FormatError, match="Colunas obrigatórias"):
        parse_events("e.csv", b"data_hora;veiculo\n2026-01-05 08:00;VEI-001\n")
    with pytest.raises(FormatError, match="Formato não suportado"):
        parse_events("e.txt", b"x")


# ---------- persistência ----------

def test_same_file_is_not_imported_twice(tmp_path):
    store = Store(tmp_path / "t.db")
    content = csv_bytes(generate_rows(DAY, 1, 10, invalid_rows=False))
    store.save_import("a.csv", content, parse_events("a.csv", content))
    with pytest.raises(DuplicateFileError):
        store.save_import("a-copia.csv", content, parse_events("a.csv", content))


def test_overlapping_files_reject_already_imported_events(tmp_path):
    store = Store(tmp_path / "t.db")
    rows = generate_rows(DAY, 1, 10, invalid_rows=False)
    first, second = csv_bytes(rows[:6]), csv_bytes(rows[4:])
    store.save_import("1.csv", first, parse_events("1.csv", first))
    out = store.save_import("2.csv", second, parse_events("2.csv", second))
    assert out["accepted"] == 4
    assert sum("já importado" in r["motivo"] for r in out["rejected"]) == 2


def test_revert_removes_events_and_keeps_audit_record(tmp_path):
    store = Store(tmp_path / "t.db")
    content = csv_bytes(generate_rows(DAY, 1, 10, invalid_rows=False))
    imp = store.save_import("a.csv", content, parse_events("a.csv", content))
    assert store.revert_import(imp["import_id"]) == 10
    assert store.events_on("2026-01-05") == []
    assert store.list_imports()[0]["status"] == "revertido"
    assert store.revert_import(imp["import_id"]) == 0  # idempotente


# ---------- relatório ----------

def test_report_counts_only_events_at_or_above_threshold(rules):
    events = [
        {"ts": "2026-01-05 08:10", "vehicle": "VEI-001", "event_type": "velocidade", "value": 95.0},
        {"ts": "2026-01-05 08:40", "vehicle": "VEI-001", "event_type": "frenagem", "value": 4.0},
        {"ts": "2026-01-05 09:00", "vehicle": "VEI-002", "event_type": "velocidade", "value": 60.0},
        {"ts": "2026-01-05 17:00", "vehicle": "VEI-002", "event_type": "marcha_lenta", "value": 12.0},
    ]
    r = build_daily_report("2026-01-05", events, rules)
    assert (r.total_events, r.occurrences) == (4, 3)
    assert r.by_type == {"velocidade": 1, "frenagem": 1, "marcha_lenta": 1}
    assert r.by_hour[8] == 2 and r.by_hour[17] == 1
    assert [x["veiculo"] for x in r.ranking] == ["VEI-001", "VEI-002"]


def test_day_without_import_is_missing_coverage_not_zero(rules):
    r = build_daily_report("2026-01-09", [], rules)
    assert r.has_data is False


# ---------- API ----------

@pytest.fixture
def client(tmp_path):
    # "testserver" é o Host do TestClient; só os testes o permitem
    return TestClient(create_app(db_path=str(tmp_path / "api.db"), allowed_hosts=("testserver",)))


def test_api_end_to_end(client, tmp_path):
    path = write_csv(generate_rows(DAY, 2, 40), tmp_path / "eventos.csv")
    with path.open("rb") as f:
        res = client.post("/api/imports", files={"file": ("eventos.csv", f, "text/csv")})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["accepted"] == 80 and len(body["rejected"]) == 5

    with path.open("rb") as f:
        assert client.post("/api/imports", files={"file": ("eventos.csv", f, "text/csv")}).status_code == 409

    assert client.get("/api/days").json() == ["2026-01-05", "2026-01-06"]
    report = client.get("/api/reports/daily/2026-01-05").json()
    assert report["has_data"] and report["total_events"] == 40

    xlsx = client.get("/api/reports/daily/2026-01-05.xlsx")
    assert xlsx.status_code == 200
    wb = load_workbook(io.BytesIO(xlsx.content))
    assert {"Resumo", "Ranking por veículo", "Por hora"} <= set(wb.sheetnames)

    pdf = client.get("/api/reports/daily/2026-01-05.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    assert client.get("/api/reports/daily/2026-01-09").json()["has_data"] is False
    assert client.get("/api/reports/daily/ontem").status_code == 422

    assert client.post(f"/api/imports/{body['import_id']}/revert").json()["eventos_removidos"] == 80
    assert client.post("/api/imports/999/revert").status_code == 404


def test_api_rejects_bad_files(client):
    res = client.post("/api/imports", files={"file": ("x.txt", b"abc", "text/plain")})
    assert res.status_code == 422
    big = b"a" * (5 * 1024 * 1024 + 10)
    assert client.post("/api/imports", files={"file": ("x.csv", big, "text/csv")}).status_code == 413


def test_index_and_static_are_served(client):
    assert "Fleet Report Demo" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


# ---------- segurança ----------

def test_xlsx_neutralizes_formula_injection(client):
    data = csv_bytes([{"data_hora": "2026-01-05 08:00", "veiculo": "=HYPERLINK(\"http://x\")", "motorista": "M",
                       "tipo_evento": "velocidade", "valor": "120"}])
    assert client.post("/api/imports", files={"file": ("f.csv", data, "text/csv")}).status_code == 201
    wb = load_workbook(io.BytesIO(client.get("/api/reports/daily/2026-01-05.xlsx").content))
    cell = wb["Ranking por veículo"]["B2"]
    assert cell.data_type == "s" and cell.value.startswith("'=")


def test_cross_origin_writes_are_blocked(client):
    data = csv_bytes(generate_rows(DAY, 1, 5, invalid_rows=False))
    res = client.post("/api/imports", files={"file": ("a.csv", data, "text/csv")},
                      headers={"Origin": "https://site-malicioso.example"})
    assert res.status_code == 403
    assert client.post("/api/imports/1/revert", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    ok = client.post("/api/imports", files={"file": ("a.csv", data, "text/csv")}, headers={"Origin": "http://testserver"})
    assert ok.status_code == 201


def test_unknown_host_header_is_rejected(client):
    assert client.get("/api/rules", headers={"Host": "rebind.example"}).status_code == 400


def test_default_hosts_exclude_testserver(tmp_path):
    from fleetdemo.api import DEFAULT_ALLOWED_HOSTS
    assert "testserver" not in DEFAULT_ALLOWED_HOSTS
    prod = TestClient(create_app(db_path=str(tmp_path / "p.db")))
    assert prod.get("/api/rules").status_code == 400  # Host "testserver" recusado
    assert prod.get("/api/rules", headers={"Host": "127.0.0.1:8000"}).status_code == 200
