"""
FastAPI web layer — REST endpoints + embedded HTML UI.

Endpoints:
  GET  /              → web UI
  POST /enrich        → single CNPJ enrichment
  POST /enrich/batch  → CSV upload, returns JSON
  GET  /companies     → list enriched companies from DB
  GET  /companies/{cnpj} → single company from DB
  GET  /export/csv    → download full DB as CSV
"""
from __future__ import annotations

import csv
import io
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from brazil_lmm.models import Company, CompanyQuery
from brazil_lmm.orchestrator import Orchestrator
from brazil_lmm.storage.database import Database
from brazil_lmm.export.exporter import _flatten, COLUMNS


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

db: Database | None = None
orchestrator: Orchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, orchestrator
    orchestrator = Orchestrator()
    if os.getenv("DATABASE_URL"):
        try:
            db = Database()
            await db.init()
        except Exception as e:
            print(f"WARNING: Database unavailable ({e}). Running without persistence.")
            db = None
    yield
    if db:
        await db.close()


app = FastAPI(title="Brazil LMM Agent", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Web UI (single-page, no framework needed)
# ---------------------------------------------------------------------------

HTML = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Brazil LMM Intelligence</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #f5f5f5; color: #1a1a1a; }
    header { background: #1a2b4a; color: white; padding: 20px 32px;
             display: flex; align-items: center; gap: 16px; }
    header h1 { font-size: 1.4rem; font-weight: 600; }
    header span { font-size: 0.85rem; opacity: 0.65; }
    main { max-width: 1100px; margin: 32px auto; padding: 0 24px; }

    .card { background: white; border-radius: 10px; padding: 24px;
            box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 24px; }
    h2 { font-size: 1rem; font-weight: 600; margin-bottom: 16px; color: #1a2b4a; }

    .tabs { display: flex; gap: 8px; margin-bottom: 20px; }
    .tab { padding: 8px 18px; border-radius: 6px; border: 1px solid #d1d5db;
           cursor: pointer; font-size: 0.9rem; background: white; }
    .tab.active { background: #1a2b4a; color: white; border-color: #1a2b4a; }

    .panel { display: none; }
    .panel.active { display: block; }

    input, select { width: 100%; padding: 10px 12px; border: 1px solid #d1d5db;
                    border-radius: 6px; font-size: 0.95rem; margin-bottom: 12px; }
    input:focus, select:focus { outline: 2px solid #1a2b4a; border-color: transparent; }

    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

    button.primary { background: #1a2b4a; color: white; border: none; padding: 11px 24px;
                     border-radius: 6px; font-size: 0.95rem; cursor: pointer; width: 100%; }
    button.primary:hover { background: #253d6a; }
    button.primary:disabled { background: #9ca3af; cursor: not-allowed; }

    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
             font-size: 0.75rem; font-weight: 600; }
    .badge-lmm { background: #dbeafe; color: #1d4ed8; }
    .badge-active { background: #dcfce7; color: #15803d; }
    .badge-inactive { background: #fee2e2; color: #b91c1c; }

    .score-bar { height: 6px; background: #e5e7eb; border-radius: 3px; margin-top: 4px; }
    .score-fill { height: 6px; background: #1a2b4a; border-radius: 3px; }

    table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
    th { text-align: left; padding: 10px 12px; background: #f9fafb;
         border-bottom: 2px solid #e5e7eb; color: #6b7280; font-weight: 600;
         font-size: 0.8rem; text-transform: uppercase; letter-spacing: .03em; }
    td { padding: 10px 12px; border-bottom: 1px solid #f3f4f6; vertical-align: top; }
    tr:hover td { background: #fafafa; }
    tr.selected td { background: #eff6ff; }

    .detail-panel { display: none; }
    .detail-panel.open { display: block; }
    .detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 12px; }
    .detail-section { background: #f9fafb; border-radius: 8px; padding: 16px; }
    .detail-section h3 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: .05em;
                          color: #6b7280; margin-bottom: 12px; }
    .kv { display: flex; justify-content: space-between; margin-bottom: 6px; font-size: 0.875rem; }
    .kv .k { color: #6b7280; }
    .kv .v { font-weight: 500; text-align: right; max-width: 55%; }
    .tag { display: inline-block; background: #e0e7ff; color: #3730a3; padding: 2px 8px;
           border-radius: 4px; font-size: 0.75rem; margin: 2px; }

    .spinner { display: none; border: 3px solid #e5e7eb; border-top-color: #1a2b4a;
               border-radius: 50%; width: 24px; height: 24px; animation: spin .7s linear infinite;
               margin: 24px auto; }
    @keyframes spin { to { transform: rotate(360deg); } }

    .empty { text-align: center; color: #9ca3af; padding: 48px; font-size: 0.9rem; }
    .notes { background: #fffbeb; border-left: 3px solid #f59e0b; padding: 10px 14px;
             border-radius: 0 6px 6px 0; font-size: 0.875rem; margin-top: 12px; }

    .actions { display: flex; gap: 8px; margin-bottom: 16px; }
    .actions a, .actions button { padding: 8px 16px; border-radius: 6px; font-size: 0.875rem;
                                   cursor: pointer; text-decoration: none; border: 1px solid #d1d5db; }
    .actions a:hover, .actions button:hover { background: #f3f4f6; }

    #toast { position: fixed; bottom: 24px; right: 24px; background: #1a2b4a; color: white;
             padding: 12px 20px; border-radius: 8px; font-size: 0.875rem;
             display: none; z-index: 1000; }
  </style>
</head>
<body>
<header>
  <div>
    <h1>Brazil LMM Intelligence</h1>
    <span>Lower Middle Market · R$50M–R$850M receita</span>
  </div>
</header>

<main>
  <!-- Input card -->
  <div class="card">
    <h2>Enriquecer empresa</h2>
    <div class="tabs">
      <button class="tab active" onclick="switchTab('single')">CNPJ único</button>
      <button class="tab" onclick="switchTab('batch')">Lote (CSV)</button>
      <button class="tab" onclick="switchTab('browse')">Ver empresas</button>
    </div>

    <!-- Single CNPJ -->
    <div id="tab-single" class="panel active">
      <div class="row">
        <div>
          <label style="font-size:.85rem;color:#6b7280;display:block;margin-bottom:4px">CNPJ</label>
          <input id="cnpj" type="text" placeholder="00.000.000/0000-00" maxlength="18">
        </div>
        <div>
          <label style="font-size:.85rem;color:#6b7280;display:block;margin-bottom:4px">Nome da empresa (opcional)</label>
          <input id="name" type="text" placeholder="Razão social ou nome fantasia">
        </div>
      </div>
      <button class="primary" onclick="enrichSingle()">Enriquecer</button>
    </div>

    <!-- Batch CSV -->
    <div id="tab-batch" class="panel">
      <p style="font-size:.875rem;color:#6b7280;margin-bottom:12px">
        CSV com colunas: <code>cnpj</code>, <code>company_name</code> (opcional), <code>website</code> (opcional)
      </p>
      <input type="file" id="csvFile" accept=".csv">
      <button class="primary" onclick="enrichBatch()">Processar lote</button>
    </div>

    <!-- Browse -->
    <div id="tab-browse" class="panel">
      <div class="row">
        <div>
          <label style="font-size:.85rem;color:#6b7280;display:block;margin-bottom:4px">Setor</label>
          <input id="filter-sector" type="text" placeholder="Ex: TI e Software">
        </div>
        <div>
          <label style="font-size:.85rem;color:#6b7280;display:block;margin-bottom:4px">UF</label>
          <select id="filter-uf">
            <option value="">Todos</option>
            <option>SP</option><option>RJ</option><option>MG</option><option>RS</option>
            <option>PR</option><option>SC</option><option>BA</option><option>CE</option>
            <option>GO</option><option>PE</option><option>DF</option><option>ES</option>
          </select>
        </div>
      </div>
      <button class="primary" onclick="loadCompanies()">Buscar no banco</button>
    </div>
  </div>

  <!-- Spinner -->
  <div class="spinner" id="spinner"></div>

  <!-- Results -->
  <div class="card" id="results-card" style="display:none">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <h2 id="results-title">Resultados</h2>
      <div class="actions">
        <button onclick="downloadCSV()">⬇ CSV</button>
      </div>
    </div>

    <table id="results-table">
      <thead>
        <tr>
          <th>Empresa</th>
          <th>Setor</th>
          <th>UF</th>
          <th>Receita est.</th>
          <th>CEO</th>
          <th>BNDES</th>
          <th>FINEP</th>
          <th>Score</th>
        </tr>
      </thead>
      <tbody id="results-body"></tbody>
    </table>

    <!-- Detail panel -->
    <div class="detail-panel" id="detail-panel">
      <hr style="margin:20px 0;border-color:#f3f4f6">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <h2 id="detail-name" style="font-size:1.1rem"></h2>
        <button onclick="closeDetail()" style="border:none;background:none;cursor:pointer;font-size:1.2rem;color:#6b7280">✕</button>
      </div>
      <div class="detail-grid" id="detail-grid"></div>
      <div class="notes" id="detail-notes" style="display:none"></div>
    </div>
  </div>
</main>

<div id="toast"></div>

<script>
let currentData = [];
let selectedCnpj = null;

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t,i) => {
    t.classList.toggle('active', ['single','batch','browse'][i] === name);
  });
  document.querySelectorAll('.panel').forEach((p,i) => {
    p.classList.toggle('active', ['tab-single','tab-batch','tab-browse'][i] === 'tab-'+name);
  });
}

function loading(on) {
  document.getElementById('spinner').style.display = on ? 'block' : 'none';
  document.querySelectorAll('button.primary').forEach(b => b.disabled = on);
}

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}

function fmt(n) {
  if (!n) return '—';
  if (n >= 1e9) return 'R$' + (n/1e9).toFixed(1) + 'B';
  if (n >= 1e6) return 'R$' + (n/1e6).toFixed(1) + 'M';
  return 'R$' + n.toLocaleString('pt-BR');
}

async function enrichSingle() {
  const cnpj = document.getElementById('cnpj').value.replace(/\\D/g,'');
  const name = document.getElementById('name').value;
  if (!cnpj) { toast('Informe o CNPJ'); return; }
  loading(true);
  try {
    const r = await fetch('/enrich', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({cnpj, company_name: name || null})
    });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    currentData = [data];
    renderTable(currentData);
    toast('Enriquecimento concluído');
  } catch(e) {
    toast('Erro: ' + e.message);
  } finally {
    loading(false);
  }
}

async function enrichBatch() {
  const file = document.getElementById('csvFile').files[0];
  if (!file) { toast('Selecione um CSV'); return; }
  loading(true);
  const form = new FormData();
  form.append('file', file);
  try {
    const r = await fetch('/enrich/batch', {method:'POST', body: form});
    if (!r.ok) throw new Error(await r.text());
    currentData = await r.json();
    renderTable(currentData);
    toast(`${currentData.length} empresas processadas`);
  } catch(e) {
    toast('Erro: ' + e.message);
  } finally {
    loading(false);
  }
}

async function loadCompanies() {
  const sector = document.getElementById('filter-sector').value;
  const uf = document.getElementById('filter-uf').value;
  loading(true);
  try {
    const params = new URLSearchParams();
    if (sector) params.set('sector', sector);
    if (uf) params.set('uf', uf);
    const r = await fetch('/companies?' + params);
    if (!r.ok) throw new Error(await r.text());
    currentData = await r.json();
    renderTable(currentData);
    toast(`${currentData.length} empresas encontradas`);
  } catch(e) {
    toast('Erro: ' + e.message);
  } finally {
    loading(false);
  }
}

function renderTable(data) {
  const card = document.getElementById('results-card');
  const body = document.getElementById('results-body');
  const title = document.getElementById('results-title');
  card.style.display = 'block';
  closeDetail();
  title.textContent = `${data.length} empresa${data.length !== 1 ? 's' : ''}`;
  body.innerHTML = '';

  if (!data.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">Nenhuma empresa encontrada</td></tr>';
    return;
  }

  data.forEach(c => {
    const score = c.outreach_score || 0;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>
        <strong>${c.nome_fantasia || c.razao_social}</strong><br>
        <span style="font-size:.75rem;color:#9ca3af">${c.cnpj}</span>
        <span class="badge badge-lmm" style="margin-left:4px">${c.size_tier}</span>
      </td>
      <td>${c.sector || '—'}</td>
      <td>${c.address_uf || '—'}</td>
      <td>${fmt(c.financials?.revenue_brl)}</td>
      <td>${c.ceo?.full_name || '—'}</td>
      <td style="text-align:center">${c.bndes_contracts?.length ? '✓ ' + c.bndes_contracts.length : '—'}</td>
      <td style="text-align:center">${c.finep_contracts?.length ? '✓ ' + c.finep_contracts.length : '—'}</td>
      <td>
        <span style="font-weight:600">${(score*100).toFixed(0)}%</span>
        <div class="score-bar"><div class="score-fill" style="width:${score*100}%"></div></div>
      </td>
    `;
    tr.style.cursor = 'pointer';
    tr.onclick = () => showDetail(c, tr);
    body.appendChild(tr);
  });
}

function showDetail(c, tr) {
  document.querySelectorAll('#results-body tr').forEach(r => r.classList.remove('selected'));
  tr.classList.add('selected');
  selectedCnpj = c.cnpj;

  document.getElementById('detail-name').textContent =
    (c.nome_fantasia || c.razao_social) + (c.is_active ? '' : ' · INATIVA');

  const grid = document.getElementById('detail-grid');
  const techAll = [
    ...c.tech_stack?.erp||[], ...c.tech_stack?.crm||[],
    ...c.tech_stack?.cloud_providers||[], ...c.tech_stack?.ecommerce||[],
    ...c.tech_stack?.analytics||[], ...c.tech_stack?.other||[]
  ];

  grid.innerHTML = `
    <div class="detail-section">
      <h3>Identidade</h3>
      ${kv('CNPJ', c.cnpj)}
      ${kv('Razão Social', c.razao_social)}
      ${kv('Setor', c.sector)}
      ${kv('Cidade / UF', [c.address_city, c.address_uf].filter(Boolean).join(' / '))}
      ${kv('Fundação', c.founded_year)}
      ${kv('Website', c.website ? `<a href="${c.website}" target="_blank">${c.website}</a>` : '—')}
    </div>
    <div class="detail-section">
      <h3>Liderança</h3>
      ${kv('CEO', c.ceo?.full_name)}
      ${kv('Cargo', c.ceo?.role)}
      ${kv('LinkedIn', c.ceo?.linkedin_url ? `<a href="${c.ceo.linkedin_url}" target="_blank">Ver perfil</a>` : '—')}
      <hr style="margin:10px 0;border-color:#e5e7eb">
      ${(c.owners||[]).map(o => kv(o.entity_type === 'PF' ? 'Sócio' : 'Empresa sócia', o.name + (o.ownership_pct ? ` (${o.ownership_pct}%)` : ''))).join('')}
    </div>
    <div class="detail-section">
      <h3>Financeiro</h3>
      ${kv('Receita estimada', fmt(c.financials?.revenue_brl))}
      ${kv('EBITDA', fmt(c.financials?.ebitda_brl))}
      ${kv('Margem EBITDA', c.financials?.ebitda_margin ? (c.financials.ebitda_margin*100).toFixed(1)+'%' : '—')}
      ${kv('Funcionários', c.financials?.headcount?.toLocaleString('pt-BR'))}
      ${kv('Fonte', c.financials?.source)}
      ${kv('Ano ref.', c.financials?.reference_year)}
    </div>
    <div class="detail-section">
      <h3>Crédito público</h3>
      ${kv('BNDES (total)', fmt(bndesTotal(c)))}
      ${kv('Contratos BNDES', c.bndes_contracts?.length || '—')}
      ${kv('Produtos BNDES', [...new Set((c.bndes_contracts||[]).map(x=>x.product))].join(', ') || '—')}
      <hr style="margin:10px 0;border-color:#e5e7eb">
      ${kv('FINEP (total)', fmt(finepTotal(c)))}
      ${kv('Contratos FINEP', c.finep_contracts?.length || '—')}
      ${kv('Programas', [...new Set((c.finep_contracts||[]).map(x=>x.program))].slice(0,3).join(', ') || '—')}
    </div>
    <div class="detail-section" style="grid-column:1/-1">
      <h3>Tecnologia</h3>
      ${techAll.length ? techAll.map(t => `<span class="tag">${t}</span>`).join('') : '<span style="color:#9ca3af">Não identificada</span>'}
      ${c.tech_stack?.inferred_from?.length ? `<p style="font-size:.75rem;color:#9ca3af;margin-top:8px">Fonte: ${c.tech_stack.inferred_from.join(', ')}</p>` : ''}
    </div>
  `;

  const notesEl = document.getElementById('detail-notes');
  if (c.outreach_notes) {
    notesEl.textContent = '💡 ' + c.outreach_notes;
    notesEl.style.display = 'block';
  } else {
    notesEl.style.display = 'none';
  }

  document.getElementById('detail-panel').classList.add('open');
  document.getElementById('detail-panel').scrollIntoView({behavior:'smooth', block:'start'});
}

function kv(k, v) {
  return `<div class="kv"><span class="k">${k}</span><span class="v">${v || '—'}</span></div>`;
}
function bndesTotal(c) { return (c.bndes_contracts||[]).reduce((s,x)=>s+(x.value_brl||0),0) || null; }
function finepTotal(c) { return (c.finep_contracts||[]).reduce((s,x)=>s+(x.value_brl||0),0) || null; }

function closeDetail() {
  document.getElementById('detail-panel').classList.remove('open');
  document.querySelectorAll('#results-body tr').forEach(r => r.classList.remove('selected'));
  selectedCnpj = null;
}

function downloadCSV() {
  if (!currentData.length) return;
  window.location.href = '/export/csv';
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def ui():
    return HTML


@app.post("/enrich")
async def enrich_single(query: CompanyQuery) -> dict:
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not initialized")
    if not query.cnpj and not query.company_name:
        raise HTTPException(400, "Provide cnpj or company_name")
    company = await orchestrator.process(query)
    if db:
        await db.upsert(company)
    return company.model_dump()


@app.post("/enrich/batch")
async def enrich_batch(file: UploadFile = File(...)) -> list[dict]:
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not initialized")

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    queries: list[CompanyQuery] = []
    for row in reader:
        queries.append(CompanyQuery(
            cnpj=row.get("cnpj") or row.get("CNPJ"),
            company_name=row.get("company_name") or row.get("razao_social"),
            website=row.get("website"),
        ))

    companies = await orchestrator.process_batch(queries)
    if db:
        await db.upsert_batch(companies)
    return [c.model_dump() for c in companies]


@app.get("/companies")
async def list_companies(
    sector: str | None = Query(None),
    uf: str | None = Query(None),
    min_score: float = Query(0.0),
    limit: int = Query(100, le=500),
) -> list[dict]:
    if not db:
        raise HTTPException(503, "Database not configured")
    companies = await db.list_lmm(sector=sector, uf=uf, min_score=min_score, limit=limit)
    return [c.model_dump() for c in companies]


@app.get("/companies/{cnpj}")
async def get_company(cnpj: str) -> dict:
    if not db:
        raise HTTPException(503, "Database not configured")
    company = await db.get(cnpj)
    if not company:
        raise HTTPException(404, f"CNPJ {cnpj} not found")
    return company.model_dump()


@app.get("/export/csv")
async def export_csv_endpoint(
    sector: str | None = Query(None),
    uf: str | None = Query(None),
) -> StreamingResponse:
    if not db:
        raise HTTPException(503, "Database not configured")

    companies = await db.list_lmm(sector=sector, uf=uf, limit=5000)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    for c in companies:
        writer.writerow(_flatten(c))

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=lmm_companies.csv"},
    )
