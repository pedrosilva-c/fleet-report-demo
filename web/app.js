// Interface da demonstração. Dados exibidos via textContent / criação de nós:
// nada vindo do arquivo importado é interpretado como HTML.
const $ = (s) => document.querySelector(s);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };

async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) throw new Error(body?.detail || `Erro ${res.status}`);
  return body;
}

async function loadImports() {
  const rows = await api("/api/imports");
  const tbody = $("#imports");
  tbody.replaceChildren();
  if (!rows.length) { const tr = el("tr"); const td = el("td", "Nenhum lote ainda.", "muted"); td.colSpan = 6; tr.append(td); tbody.append(tr); return; }
  for (const r of rows) {
    const tr = el("tr");
    tr.append(el("td", r.id), el("td", r.filename), el("td", r.accepted), el("td", r.rejected), el("td", r.status));
    const td = el("td");
    if (r.status === "ativo") {
      const b = el("button", "Reverter");
      b.type = "button";
      b.addEventListener("click", async () => {
        if (!confirm(`Reverter o lote ${r.id}? Os eventos dele serão removidos.`)) return;
        await api(`/api/imports/${r.id}/revert`, { method: "POST" });
        await refresh();
      });
      td.append(b);
    }
    tr.append(td);
    tbody.append(tr);
  }
}

async function loadDays() {
  const days = await api("/api/days");
  const sel = $("#day");
  const current = sel.value;
  sel.replaceChildren(...days.map((d) => { const o = el("option", d); o.value = d; return o; }));
  if (!days.length) { const o = el("option", "sem dados"); o.value = ""; sel.append(o); }
  if (days.includes(current)) sel.value = current;
  await loadReport();
}

async function loadReport() {
  const day = $("#day").value;
  const box = $("#report");
  box.replaceChildren();
  $("#xlsx").href = day ? `/api/reports/daily/${day}.xlsx` : "#";
  $("#pdf").href = day ? `/api/reports/daily/${day}.pdf` : "#";
  if (!day) { box.append(el("p", "Importe um arquivo para ver o relatório.", "muted")); return; }
  const r = await api(`/api/reports/daily/${day}`);
  if (!r.has_data) { box.append(el("p", "Sem dados importados para este dia (cobertura ausente).", "warn")); return; }

  const stats = el("div", undefined, "stats");
  const stat = (label, value) => { const s = el("div", undefined, "stat"); s.append(el("b", value), el("span", label, "muted small")); return s; };
  stats.append(stat("eventos analisados", r.total_events), stat("ocorrências acima do limite", r.occurrences));
  for (const [t, n] of Object.entries(r.by_type)) stats.append(stat(r.labels[t], n));
  box.append(stats);

  box.append(el("h3", "Ranking por veículo"));
  const table = el("table");
  const head = el("tr"); ["Pos.", "Veículo", "Total", "Velocidade", "Frenagem", "Marcha lenta"].forEach((h) => head.append(el("th", h)));
  const thead = el("thead"); thead.append(head);
  const tbody = el("tbody");
  r.ranking.forEach((v, i) => { const tr = el("tr"); [i + 1, v.veiculo, v.total, v.velocidade, v.frenagem, v.marcha_lenta].forEach((c) => tr.append(el("td", c))); tbody.append(tr); });
  table.append(thead, tbody);
  box.append(table);

  box.append(el("h3", "Ocorrências por hora"));
  const peak = Math.max(1, ...r.by_hour);
  const hours = el("table", undefined, "hours");
  const hb = el("tbody");
  r.by_hour.forEach((n, h) => {
    if (!n) return;
    const tr = el("tr"); tr.append(el("td", `${String(h).padStart(2, "0")}:00`));
    const td = el("td"); const bar = el("div", undefined, "bar"); bar.style.width = `${(100 * n) / peak}%`; bar.setAttribute("aria-hidden", "true");
    td.append(bar); tr.append(td, el("td", n)); hb.append(tr);
  });
  hours.append(hb);
  box.append(hours);
}

async function refresh() { await loadImports(); await loadDays(); }

$("#upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#import-result");
  out.replaceChildren();
  const fd = new FormData(); fd.append("file", $("#file").files[0]);
  try {
    const r = await api("/api/imports", { method: "POST", body: fd });
    out.append(el("p", `Lote ${r.import_id}: ${r.accepted} linhas aceitas, ${r.rejected.length} rejeitadas.`, "ok"));
    if (r.rejected.length) {
      const d = el("details"); d.append(el("summary", "Ver linhas rejeitadas e motivos"));
      const ul = el("ul"); r.rejected.forEach((x) => ul.append(el("li", `${x.linha ? "Linha " + x.linha + ": " : ""}${x.motivo}`)));
      d.append(ul); out.append(d);
    }
    await refresh();
  } catch (err) { out.append(el("p", err.message, "warn")); }
});
$("#day").addEventListener("change", loadReport);

api("/api/rules").then((r) => {
  $("#rules").textContent = `Regras (versão ${r.version}, valores ilustrativos): ` +
    Object.values(r.rules).map((x) => `${x.rotulo} ≥ ${x.limite} ${x.unidade}`).join(" · ");
});
refresh();
