"""API local (FastAPI) e interface web estática.

Executar:  python -m uvicorn fleetdemo.api:create_app --factory --port 8000
Variáveis: FLEETDEMO_DB (caminho do SQLite), FLEETDEMO_RULES (JSON de regras).
A aplicação escuta só em 127.0.0.1 por padrão e não tem autenticação:
é uma demonstração local, não um serviço para rede.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .importer import FormatError, parse_events
from .reports import build_daily_report, to_pdf, to_xlsx
from .rules import DEFAULT_RULES_PATH, load_rules
from .storage import DuplicateFileError, Store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def create_app(db_path: str | None = None, rules_path: str | None = None) -> FastAPI:
    store = Store(db_path or os.environ.get("FLEETDEMO_DB", "fleetdemo.db"))
    rules = load_rules(rules_path or os.environ.get("FLEETDEMO_RULES", DEFAULT_RULES_PATH))
    app = FastAPI(title="Fleet Report Demo", version="0.1.0",
                  description="Demonstração com dados fictícios: importação conferida e relatórios de telemetria.")

    @app.get("/api/rules")
    def get_rules():
        return {"version": rules.version,
                "rules": {t: {"limite": r.threshold, "unidade": r.unit, "rotulo": r.label} for t, r in rules.rules.items()}}

    @app.post("/api/imports", status_code=201)
    async def upload(file: UploadFile = File(...)):
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Arquivo maior que 5 MB.")
        name = Path(file.filename or "arquivo").name
        try:
            parsed = parse_events(name, content)
        except FormatError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return store.save_import(name, content, parsed)
        except DuplicateFileError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/imports")
    def list_imports():
        return store.list_imports()

    @app.post("/api/imports/{import_id}/revert")
    def revert(import_id: int):
        try:
            return {"import_id": import_id, "eventos_removidos": store.revert_import(import_id)}
        except KeyError:
            raise HTTPException(404, "Lote não encontrado.")

    @app.get("/api/days")
    def days():
        return store.days()

    def _report(day: str):
        if not DAY_RE.match(day):
            raise HTTPException(422, "Dia deve estar no formato AAAA-MM-DD.")
        return build_daily_report(day, store.events_on(day), rules)

    @app.get("/api/reports/daily/{day}.xlsx")
    def daily_xlsx(day: str):
        return Response(to_xlsx(_report(day)),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="relatorio-{day}.xlsx"'})

    @app.get("/api/reports/daily/{day}.pdf")
    def daily_pdf(day: str):
        return Response(to_pdf(_report(day)), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="relatorio-{day}.pdf"'})

    # rota genérica por último: senão "/2026-01-05.xlsx" cairia aqui
    @app.get("/api/reports/daily/{day}")
    def daily(day: str):
        return _report(day).as_dict()

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app

