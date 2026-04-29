#!/usr/bin/env python3
"""
Build dashboard HTML standalone qualita-vita-italia.

Genera:
- output/dashboard.html: pagina standalone con Plotly mappa Italia + tabelle + calcolatore
- output/wordpress.html: snippet pronto per page-macro-dove-vivere.php
- output/data/comuni_centroidi.csv: cache centroidi lat/lon per ogni comune

La mappa e' Plotly scatter_mapbox con un cerchio per ogni comune che ha dati OMI:
- color = indice qualita (rosso basso → verde alto)
- size = log(n_contribuenti) (citta grandi piu' visibili)
- hover = nome, reddito mediana, affitto OMI, indice

Tabelle:
- Top 30 / Bottom 30 per indice qualita
- Filtri: regione, dimensione comune, profilo familiare

Calcolatore reddito sostenibile: dropdown comune + radio profilo + radio modalita →
mostra lordo richiesto, residuo dalla mediana, mq.
"""
from __future__ import annotations
import csv
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dashboard")

ROOT = Path(__file__).resolve().parent.parent
RAW_GEO = ROOT / "dati" / "raw" / "geo"
PROC = ROOT / "dati" / "processed"
OUT = ROOT / "output"
OUT_DATA = OUT / "data"


# =============================================================================
# Centroidi (calcolo da GeoJSON comuni)
# =============================================================================
def _coord_iter(geom):
    """Itera tutte le (lon, lat) di una geometria GeoJSON (Polygon/MultiPolygon)."""
    if not geom:
        return
    t = geom.get("type")
    coords = geom.get("coordinates", [])
    if t == "Polygon":
        for ring in coords:
            for x, y in ring:
                yield x, y
    elif t == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                for x, y in ring:
                    yield x, y


def compute_centroidi(geo_path: Path, out_csv: Path) -> dict[str, tuple[float, float]]:
    """Centroidi semplici (media coordinate ring esterno) per ogni comune.
    Cache in CSV. Chiave: codice ISTAT 6 cifre con leading zero."""
    if out_csv.exists():
        with out_csv.open(newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            return {row["codice_istat"]: (float(row["lat"]), float(row["lon"])) for row in r}

    log.info("calcolo centroidi da %s", geo_path)
    g = json.loads(geo_path.read_text())
    out: dict[str, tuple[float, float]] = {}
    for feat in g.get("features", []):
        props = feat.get("properties", {}) or {}
        # Schema OpenPolis: 'com_istat_code' (6 cifre) o 'pro_com_t' (numerico)
        cod = (props.get("com_istat_code")
               or props.get("pro_com_t")
               or props.get("PRO_COM_T")
               or props.get("PRO_COM"))
        if not cod:
            continue
        cod = str(cod).strip().zfill(6)
        xs, ys = [], []
        for x, y in _coord_iter(feat.get("geometry")):
            xs.append(x)
            ys.append(y)
        if not xs:
            continue
        # Centroide naive: media coordinate. Sufficiente per markers.
        lon = sum(xs) / len(xs)
        lat = sum(ys) / len(ys)
        out[cod] = (lat, lon)

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["codice_istat", "lat", "lon"])
        for cod, (lat, lon) in sorted(out.items()):
            w.writerow([cod, f"{lat:.6f}", f"{lon:.6f}"])
    log.info("scritto %s (%d centroidi)", out_csv, len(out))
    return out


# =============================================================================
# HTML rendering
# =============================================================================
DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>Dove si vive bene in Italia — qualità della vita per comune</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0; padding: 0;
         background: #fafafa; color: #111; }
  .container { max-width: 1200px; margin: 0 auto; padding: 1rem; }
  h1 { font-size: 1.6rem; margin-bottom: 0.3rem; }
  h2 { font-size: 1.25rem; margin-top: 2rem; border-bottom: 1px solid #ddd; padding-bottom: 0.3rem; }
  .meta { color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }
  .controls { background: #fff; padding: 0.8rem 1rem; border: 1px solid #e5e5e5; border-radius: 6px;
              margin-bottom: 1rem; display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }
  .controls label { font-size: 0.9rem; }
  .controls select, .controls input[type=number] { padding: 0.3rem; font-size: 0.9rem; }
  #map { width: 100%; height: 600px; background: #fff; border: 1px solid #e5e5e5; border-radius: 6px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1rem; }
  @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
  table { width: 100%; border-collapse: collapse; background: #fff; }
  th, td { padding: 0.45rem 0.6rem; text-align: left; font-size: 0.85rem; border-bottom: 1px solid #eee; }
  th { background: #f0f0f0; font-weight: 600; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .calc { background: #fff; padding: 1rem; border: 1px solid #e5e5e5; border-radius: 6px; margin-top: 1rem; }
  .calc-row { display: grid; grid-template-columns: max-content 1fr; gap: 0.5rem 1rem; align-items: center; margin-bottom: 0.5rem; }
  .calc-out { background: #f7f7f0; padding: 1rem; border-radius: 6px; margin-top: 1rem; font-size: 0.95rem; }
  .badge { display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 0.75rem; font-weight: 600; }
  .badge-good { background: #d8f3dc; color: #1b4332; }
  .badge-bad { background: #ffd6d6; color: #6a0000; }
  .badge-warn { background: #fff2cc; color: #6a5a00; }
  .footer { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #ddd; color: #666; font-size: 0.8rem; }
  a { color: #0066cc; }
</style>
</head>
<body>
<div class="container">
<h1>Dove si vive bene in Italia</h1>
<p class="meta">
  Indice qualità della vita per comune. Combina reddito mediano (MEF dichiarazioni IRPEF __ANNO__),
  prezzi di acquisto e affitti OMI (Agenzia Entrate, semestre __SEM__), reddito sostenibile per
  diversi profili familiari. Dashboard preliminare con __N_COMUNI__ comuni: include capoluoghi e
  città grandi. Bulk completo in elaborazione.
</p>

<div class="controls">
  <label>Indicatore mappa:
    <select id="indicatore">
      <option value="indice_qualita" selected>Indice qualità (composito)</option>
      <option value="reddito_mediana">Reddito mediano</option>
      <option value="affitto_eur_mq_mese_med">Affitto € /mq mese</option>
      <option value="prezzo_acq_eur_mq_med">Prezzo acquisto € /mq</option>
      <option value="ratio_p90_p10">Disuguaglianza P90/P10</option>
      <option value="residuo_single_affitto">Residuo netto annuo (single)</option>
    </select>
  </label>
  <label>Filtra regione:
    <select id="regione">
      <option value="">Tutte</option>
    </select>
  </label>
</div>

<div id="map"></div>

<h2>Classifica</h2>
<div class="grid">
  <div>
    <h3 style="margin-top: 0">Top 30 — vivibilità</h3>
    <table id="top-tab">
      <thead><tr><th>#</th><th>Comune</th><th>Pr</th><th class="num">Indice</th><th class="num">Mediana €</th><th class="num">Affitto €/mq</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
  <div>
    <h3 style="margin-top: 0">Bottom 30 — vivibilità</h3>
    <table id="bot-tab">
      <thead><tr><th>#</th><th>Comune</th><th>Pr</th><th class="num">Indice</th><th class="num">Mediana €</th><th class="num">Affitto €/mq</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
</div>

<h2>Calcolatore reddito sostenibile</h2>
<p>Quanto reddito lordo familiare serve per coprire le spese in un comune scelto, dato un profilo familiare?</p>
<div class="calc">
  <div class="calc-row">
    <label for="calc-comune">Comune:</label>
    <select id="calc-comune"></select>
    <label for="calc-profilo">Profilo:</label>
    <select id="calc-profilo">
      <option value="single">1 adulto</option>
      <option value="coppia" selected>Coppia, 1 stipendio</option>
      <option value="coppia2">Coppia, 2 stipendi</option>
      <option value="coppia_1f">Coppia + 1 figlio, 1 stip.</option>
      <option value="coppia_2f">Coppia + 2 figli, 2 stip.</option>
    </select>
    <label for="calc-modalita">Casa:</label>
    <select id="calc-modalita">
      <option value="affitto" selected>Affitto</option>
      <option value="proprieta">Già proprietario</option>
    </select>
  </div>
  <div id="calc-out" class="calc-out">Seleziona un comune per vedere il calcolo.</div>
</div>

<h2>Dettaglio comune</h2>
<div id="dettaglio" class="calc">Clicca un punto sulla mappa o un comune in classifica per vedere il dettaglio.</div>

<div class="footer">
  Fonti: <a href="https://www1.finanze.gov.it/finanze/analisi_stat/public/v_4_0_0/contenuti/" target="_blank">MEF Dipartimento delle Finanze</a> (dichiarazioni IRPEF su base comunale, anno __ANNO__);
  <a href="https://wwwt.agenziaentrate.gov.it/geopoi_omi/index.htm" target="_blank">Agenzia delle Entrate OMI</a>
  (Quotazioni Immobiliari, semestre __SEM__).
  Codice e dati: <a href="https://github.com/" target="_blank">github.com/qualita-vita-italia</a>.
  Indice qualità: 60% residuo netto annuo + 40% accessibilità casa (versione preliminare; verranno
  aggiunti criminalità e servizi BES quando disponibili). Dati per __N_COMUNI__ comuni; copertura
  bulk completa in elaborazione.
</div>
</div>

<script>
const PAYLOAD = __PAYLOAD__;
const CENTROIDI = __CENTROIDI__;
const PROFILI = PAYLOAD.meta.profili;
const PANIERE = PAYLOAD.meta.paniere_non_casa;
const COMUNI = PAYLOAD.comuni;

// IRPEF 2025 — clientside per il calcolatore
const SCAGLIONI = [[28000, 0.23], [50000, 0.35], [Infinity, 0.43]];
const ADD_REG = 0.0173, ADD_COM = 0.005, DETR_BASE = 1955, DETR_FIG = 950;
function irpefLorda(r) {
  let imp = 0, prev = 0;
  for (const [s, a] of SCAGLIONI) { if (r <= prev) break; const cap = Math.min(r, s); imp += (cap - prev) * a; prev = cap; }
  return imp;
}
function detrazioneDip(r) {
  if (r <= 15000) return DETR_BASE;
  if (r <= 28000) return 1910 + 1190 * (28000 - r) / 13000;
  if (r <= 50000) return 1910 * (50000 - r) / 22000;
  return 0;
}
function nettoDaLordo(lordo, nFigli) {
  const ip = Math.max(0, irpefLorda(lordo) - detrazioneDip(lordo) - nFigli * DETR_FIG);
  const add = lordo * (ADD_REG + ADD_COM);
  return lordo - ip - add;
}
function lordoPerNetto(nettoTarget, nFigli, nPercettori) {
  if (nettoTarget <= 0) return 0;
  let lo = 0, hi = 250000;
  const nettoP = nettoTarget / nPercettori;
  for (let i = 0; i < 40; i++) { const m = (lo + hi) / 2; if (nettoDaLordo(m, nFigli) < nettoP) lo = m; else hi = m; }
  return ((lo + hi) / 2) * nPercettori;
}
function costoCasaAnnuo(prof, modalita, affitto, prezzoAcq) {
  if (modalita === "affitto") return affitto ? prof.mq * affitto * 12 : null;
  if (modalita === "mutuo") return prezzoAcq ? prof.mq * prezzoAcq * 0.059 : null;
  if (modalita === "proprieta") return 1500;
  return null;
}
function fmt(n) { if (n == null || isNaN(n)) return "—"; return Math.round(n).toLocaleString("it-IT") + " €"; }
function fmtN(n, dec=2) { if (n == null || isNaN(n)) return "—"; return n.toFixed(dec); }

// =====================  Setup regioni  =====================
const regioni = [...new Set(COMUNI.map(c => c.regione).filter(Boolean))].sort();
const regSel = document.getElementById("regione");
for (const r of regioni) { const o = document.createElement("option"); o.value = r; o.text = r; regSel.appendChild(o); }

// =====================  Comuni dropdown  =====================
const comuneSel = document.getElementById("calc-comune");
COMUNI.sort((a, b) => a.comune.localeCompare(b.comune)).forEach(c => {
  const o = document.createElement("option");
  o.value = c.codice_istat;
  o.text = c.comune + " (" + c.sigla_provincia + ")";
  comuneSel.appendChild(o);
});

// =====================  Mappa  =====================
function buildMap(filtro) {
  const subset = COMUNI.filter(c => CENTROIDI[c.codice_istat] && (!filtro || c.regione === filtro));
  const indic = document.getElementById("indicatore").value;
  const z = subset.map(c => c[indic]);
  const text = subset.map(c =>
    `<b>${c.comune}</b> (${c.sigla_provincia})<br>` +
    `Reddito mediano: ${fmt(c.reddito_mediana)}<br>` +
    `Affitto: ${fmtN(c.affitto_eur_mq_mese_med)} €/mq mese<br>` +
    `Prezzo acq: ${fmt(c.prezzo_acq_eur_mq_med)}/mq<br>` +
    `Indice qualità: ${fmtN(c.indice_qualita, 1)}/100<br>` +
    `<i>Click per dettaglio</i>`
  );
  const lats = subset.map(c => CENTROIDI[c.codice_istat][0]);
  const lons = subset.map(c => CENTROIDI[c.codice_istat][1]);
  const sizes = subset.map(c => Math.max(8, Math.min(40, Math.log10(c.n_contribuenti || 1000) * 6)));

  const trace = {
    type: "scattermapbox",
    mode: "markers",
    lat: lats,
    lon: lons,
    marker: {
      size: sizes,
      color: z,
      colorscale: indic === "indice_qualita" || indic.startsWith("residuo") || indic === "reddito_mediana"
                  ? "RdYlGn" : "RdYlGn_r",
      showscale: true,
      colorbar: { title: indic, thickness: 15 }
    },
    text: text,
    hovertemplate: "%{text}<extra></extra>",
    customdata: subset.map(c => c.codice_istat),
  };
  const layout = {
    mapbox: { style: "open-street-map", center: { lat: 42.5, lon: 12.5 }, zoom: 5.2 },
    margin: { t: 10, b: 10, l: 10, r: 10 },
    height: 600,
  };
  Plotly.newPlot("map", [trace], layout, { displayModeBar: false }).then(gd => {
    gd.on("plotly_click", e => mostraDettaglio(e.points[0].customdata));
  });
}
buildMap("");
document.getElementById("indicatore").addEventListener("change", () => buildMap(regSel.value));
document.getElementById("regione").addEventListener("change", () => buildMap(regSel.value));

// =====================  Tabelle Top/Bottom  =====================
function tabella(arr, sel) {
  const tb = document.querySelector(sel + " tbody");
  tb.innerHTML = "";
  arr.forEach((c, i) => {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    tr.innerHTML =
      `<td>${i+1}</td><td>${c.comune}</td><td>${c.sigla_provincia}</td>` +
      `<td class="num">${fmtN(c.indice_qualita, 1)}</td>` +
      `<td class="num">${fmt(c.reddito_mediana)}</td>` +
      `<td class="num">${fmtN(c.affitto_eur_mq_mese_med)}</td>`;
    tr.addEventListener("click", () => mostraDettaglio(c.codice_istat));
    tb.appendChild(tr);
  });
}
const sortedDesc = [...COMUNI].filter(c => c.indice_qualita != null).sort((a, b) => b.indice_qualita - a.indice_qualita);
tabella(sortedDesc.slice(0, 30), "#top-tab");
tabella(sortedDesc.slice(-30).reverse(), "#bot-tab");

// =====================  Calcolatore  =====================
function calcola() {
  const cod = document.getElementById("calc-comune").value;
  const profKey = document.getElementById("calc-profilo").value;
  const modalita = document.getElementById("calc-modalita").value;
  const c = COMUNI.find(x => x.codice_istat === cod);
  const p = PROFILI[profKey];
  if (!c || !p) { document.getElementById("calc-out").innerHTML = "Seleziona un comune."; return; }
  const paniere = PANIERE[profKey];
  const casa = costoCasaAnnuo(p, modalita, c.affitto_eur_mq_mese_med, c.prezzo_acq_eur_mq_med);
  if (casa == null) { document.getElementById("calc-out").innerHTML = "Dati casa non disponibili."; return; }
  const spesa = paniere + casa;
  const lordo = lordoPerNetto(spesa, p.figli, p.percettori);
  const nettoMediana = nettoDaLordo(c.reddito_mediana || 0, p.figli);
  const residuo = nettoMediana - spesa;
  const colore = residuo > 0 ? "badge-good" : "badge-bad";
  document.getElementById("calc-out").innerHTML =
    `<b>${c.comune} (${c.sigla_provincia})</b> — profilo: ${p.label}, ${p.mq} mq, ${modalita}<br><br>` +
    `Spesa annua casa: <b>${fmt(casa)}</b><br>` +
    `Spesa annua altre voci (paniere): <b>${fmt(paniere)}</b><br>` +
    `Spesa totale annua: <b>${fmt(spesa)}</b><br><br>` +
    `Reddito lordo familiare necessario: <b>${fmt(lordo)}</b><br>` +
    `Reddito mediano comunale (lordo): <b>${fmt(c.reddito_mediana)}</b> &nbsp; (netto stimato: ${fmt(nettoMediana)})<br><br>` +
    `<span class="badge ${colore}">Residuo annuo dalla mediana: ${fmt(residuo)}</span>`;
}
document.getElementById("calc-comune").addEventListener("change", calcola);
document.getElementById("calc-profilo").addEventListener("change", calcola);
document.getElementById("calc-modalita").addEventListener("change", calcola);

// =====================  Dettaglio comune  =====================
function mostraDettaglio(cod) {
  const c = COMUNI.find(x => x.codice_istat === cod);
  if (!c) return;
  document.getElementById("calc-comune").value = cod;
  calcola();
  const html =
    `<h3 style="margin-top: 0">${c.comune} <small>(${c.sigla_provincia})</small></h3>` +
    `<table>` +
    `<tr><th>Distribuzione redditi</th><td class="num">P10: ${fmt(c.reddito_p10)}</td>` +
    `<td class="num">Mediana: ${fmt(c.reddito_mediana)}</td>` +
    `<td class="num">P90: ${fmt(c.reddito_p90)}</td></tr>` +
    `<tr><th>Disuguaglianza</th><td colspan="3">P90/P10 = ${fmtN(c.ratio_p90_p10, 2)}` +
    `&nbsp;&nbsp; (% sotto 15k: ${fmtN(c.pct_sotto_15k, 1)}%, ` +
    `% sopra 55k: ${fmtN(c.pct_sopra_55k, 1)}%)</td></tr>` +
    `<tr><th>OMI residenziale</th><td colspan="3">Acquisto: ${fmt(c.prezzo_acq_eur_mq_med)}/mq` +
    `&nbsp;&nbsp; Affitto: ${fmtN(c.affitto_eur_mq_mese_med, 2)} €/mq mese</td></tr>` +
    `<tr><th>Reddito sostenibile (affitto)</th>` +
    `<td class="num">single: ${fmt(c.rs_single_affitto)}</td>` +
    `<td class="num">coppia: ${fmt(c.rs_coppia_affitto)}</td>` +
    `<td class="num">+2 figli: ${fmt(c.rs_coppia_2f_affitto)}</td></tr>` +
    `<tr><th>Indice qualità</th><td colspan="3"><b>${fmtN(c.indice_qualita, 1)}/100</b></td></tr>` +
    `</table>`;
  document.getElementById("dettaglio").innerHTML = html;
  document.getElementById("dettaglio").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// init calcolatore
if (COMUNI.length) { document.getElementById("calc-comune").value = COMUNI[0].codice_istat; calcola(); }
</script>
</body>
</html>
"""


def build_dashboard():
    payload_path = OUT_DATA / "comuni.json"
    if not payload_path.exists():
        log.error("manca %s — esegui 07_join_indicatori.py", payload_path)
        return 1
    payload = json.loads(payload_path.read_text())

    # Centroidi
    geo_path = RAW_GEO / "comuni.geojson"
    centroidi_csv = PROC / "comuni_centroidi.csv"
    centroidi = compute_centroidi(geo_path, centroidi_csv)

    # Costruisci dict {codice: [lat, lon]} solo per comuni con dati nel payload
    cod_in_payload = {c["codice_istat"] for c in payload["comuni"]}
    cent_subset = {k: [round(v[0], 5), round(v[1], 5)]
                   for k, v in centroidi.items() if k in cod_in_payload}
    log.info("centroidi matchati: %d/%d", len(cent_subset), len(cod_in_payload))
    missing = cod_in_payload - set(cent_subset.keys())
    if missing:
        log.warning("centroide mancante per %d comuni (es: %s)",
                    len(missing), ", ".join(list(missing)[:5]))

    anno = payload["meta"].get("anno_redditi") or "—"
    sem = payload["meta"].get("semestre_omi") or "—"
    n_comuni = len(payload["comuni"])

    html = (DASHBOARD_TEMPLATE
            .replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
            .replace("__CENTROIDI__", json.dumps(cent_subset))
            .replace("__ANNO__", str(anno))
            .replace("__SEM__", str(sem))
            .replace("__N_COMUNI__", str(n_comuni)))

    out_html = OUT / "dashboard.html"
    out_html.write_text(html, encoding="utf-8")
    log.info("scritto %s (%.1f KB)", out_html, out_html.stat().st_size / 1024)

    # Versione WP: solo body senza <html>/<head> tag (per inserzione in blocco WP HTML personalizzato)
    body_start = html.index("<body>") + len("<body>")
    body_end = html.index("</body>")
    style_start = html.index("<style>")
    style_end = html.index("</style>") + len("</style>")
    plotly_idx = html.index("<script src=\"https://cdn.plot.ly")
    plotly_end = html.index("</script>", plotly_idx) + len("</script>")
    wp_html = (
        html[style_start:style_end] + "\n" +
        html[plotly_idx:plotly_end] + "\n" +
        html[body_start:body_end]
    )
    out_wp = OUT / "wordpress.html"
    out_wp.write_text(wp_html, encoding="utf-8")
    log.info("scritto %s (%.1f KB)", out_wp, out_wp.stat().st_size / 1024)
    return 0


if __name__ == "__main__":
    sys.exit(build_dashboard())
