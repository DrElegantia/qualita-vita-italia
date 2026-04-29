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
<p>Quanto reddito lordo familiare serve per coprire le spese in un comune scelto, dato un profilo familiare?
Sotto il calcolo "quanto serve" puoi anche <b>simulare un tuo reddito</b> e vedere quanto resta in regime IRPEF ordinario o flat tax 15% (forfettario).</p>
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

  <div style="border-top: 1px solid #e5e5e5; margin-top: 1rem; padding-top: 0.8rem;">
    <div class="calc-row">
      <label for="sim-reddito">Simula il tuo reddito imponibile lordo (€/anno):</label>
      <input type="number" id="sim-reddito" min="0" max="500000" step="1000" value="30000" style="width: 140px; padding: 0.3rem;">
      <label for="sim-regime">Regime:</label>
      <select id="sim-regime">
        <option value="ordinario" selected>IRPEF ordinario (dipendente)</option>
        <option value="flat15">Flat tax 15% (forfettario)</option>
        <option value="flat5">Flat tax 5% (forfettario startup, primi 5 anni)</option>
      </select>
    </div>
    <div id="sim-out" class="calc-out" style="background: #eef2f7;">Inserisci un reddito per simulare.</div>
    <p style="font-size: 0.78rem; color: #666; margin-top: 0.5rem;">
      Note: l'imponibile IRPEF è già al netto dei contributi previdenziali. Per il regime ordinario applichiamo
      IRPEF a scaglioni 23/35/43% + detrazioni dipendente + addizionali regionale e comunale medie. Per il
      forfettario applichiamo l'imposta sostitutiva sull'imponibile (no addizionali, no detrazioni).
      Il regime forfettario richiede ricavi entro <b>85.000 €/anno</b>: oltre questa soglia non è applicabile.
    </p>
  </div>
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
// Regime forfettario: imposta sostitutiva (15% std, 5% startup), no detrazioni, no addizionali.
// Soglia max ricavi forfettario: 85.000 €/anno.
const FLAT_LIMIT = 85000;
function nettoFlatTax(lordo, aliquota) {
  if (lordo > FLAT_LIMIT) return null;
  return lordo * (1 - aliquota);
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

function simulaReddito() {
  const cod = document.getElementById("calc-comune").value;
  const profKey = document.getElementById("calc-profilo").value;
  const modalita = document.getElementById("calc-modalita").value;
  const regime = document.getElementById("sim-regime").value;
  const reddito = parseFloat(document.getElementById("sim-reddito").value) || 0;
  const c = COMUNI.find(x => x.codice_istat === cod);
  const p = PROFILI[profKey];
  if (!c || !p) { document.getElementById("sim-out").innerHTML = "Seleziona un comune."; return; }
  const paniere = PANIERE[profKey];
  const casa = costoCasaAnnuo(p, modalita, c.affitto_eur_mq_mese_med, c.prezzo_acq_eur_mq_med);
  const spesa = casa != null ? paniere + casa : null;

  // Calcolo netto per entrambi i regimi (per il confronto)
  const nettoOrd = nettoDaLordo(reddito, p.figli);
  const nettoF15 = nettoFlatTax(reddito, 0.15);
  const nettoF05 = nettoFlatTax(reddito, 0.05);
  const nettoSel = regime === "ordinario" ? nettoOrd
                 : regime === "flat15"   ? nettoF15
                                          : nettoF05;
  const labelRegime = regime === "ordinario" ? "IRPEF ordinario"
                    : regime === "flat15"   ? "Flat tax 15%"
                                              : "Flat tax 5% (startup)";

  // Avviso forfettario se sopra soglia
  let avviso = "";
  if (regime !== "ordinario" && reddito > FLAT_LIMIT) {
    avviso = `<div style="background: #ffd6d6; padding: 0.5rem; margin-top: 0.5rem; border-radius: 4px; font-size: 0.85rem;">` +
      `⚠ Sopra soglia forfettario (${fmt(FLAT_LIMIT)}). In regime ordinario, netto: <b>${fmt(nettoOrd)}</b>.</div>`;
  }

  let residuoBlock = "";
  if (spesa != null && nettoSel != null) {
    const residuo = nettoSel - spesa;
    const colore = residuo > 0 ? "badge-good" : "badge-bad";
    residuoBlock = `<br>Spesa annua nel comune: <b>${fmt(spesa)}</b> ` +
                   `(casa ${fmt(casa)} + paniere ${fmt(paniere)})<br>` +
                   `<span class="badge ${colore}">Residuo netto annuo: ${fmt(residuo)}</span>`;
  } else if (spesa == null) {
    residuoBlock = `<br><i>Dato OMI casa non disponibile per ${c.comune}.</i>`;
  }

  let confrontoBlock = "";
  if (reddito <= FLAT_LIMIT) {
    const diff = (nettoF15 || 0) - nettoOrd;
    const segno = diff >= 0 ? "+" : "";
    confrontoBlock =
      `<br><br><div style="font-size: 0.9rem;"><b>Confronto regimi su ${fmt(reddito)} lordo:</b><br>` +
      `· IRPEF ordinario → netto <b>${fmt(nettoOrd)}</b> (aliquota effettiva ${((1 - nettoOrd / reddito) * 100).toFixed(1)}%)<br>` +
      `· Flat tax 15%   → netto <b>${fmt(nettoF15)}</b> ` +
      `<span class="badge ${diff >= 0 ? 'badge-good' : 'badge-bad'}">${segno}${fmt(diff)} vs ordinario</span><br>` +
      `· Flat tax 5%    → netto <b>${fmt(nettoF05)}</b> (solo startup, primi 5 anni)</div>`;
  } else {
    confrontoBlock = `<br><br><div style="font-size: 0.9rem;"><b>Sopra ${fmt(FLAT_LIMIT)}: solo regime ordinario applicabile.</b><br>` +
      `Netto ordinario: <b>${fmt(nettoOrd)}</b> (aliquota effettiva ${((1 - nettoOrd / reddito) * 100).toFixed(1)}%)</div>`;
  }

  document.getElementById("sim-out").innerHTML =
    `<b>Simulazione: ${fmt(reddito)} imponibile in ${c.comune} (${c.sigla_provincia})</b><br>` +
    `Profilo: ${p.label}, ${p.mq} mq, ${modalita}, regime <b>${labelRegime}</b><br><br>` +
    `Reddito netto stimato (regime selezionato): <b>${fmt(nettoSel)}</b>` +
    residuoBlock +
    avviso +
    confrontoBlock;
}
document.getElementById("sim-reddito").addEventListener("input", simulaReddito);
document.getElementById("sim-regime").addEventListener("change", simulaReddito);
document.getElementById("calc-comune").addEventListener("change", simulaReddito);
document.getElementById("calc-profilo").addEventListener("change", simulaReddito);
document.getElementById("calc-modalita").addEventListener("change", simulaReddito);

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
if (COMUNI.length) {
  document.getElementById("calc-comune").value = COMUNI[0].codice_istat;
  calcola();
  simulaReddito();
}
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

    # Bbox regioni e province (per auto-zoom e filtro provincia in cascata)
    cod_to_meta = {c["codice_istat"]: c for c in payload["comuni"]}
    bbox_reg: dict[str, list[float]] = {}   # regione → [lat_min, lat_max, lon_min, lon_max]
    bbox_prov: dict[str, list[float]] = {}  # sigla   → [lat_min, lat_max, lon_min, lon_max]
    reg_province: dict[str, set[str]] = {}  # regione → set(sigle)
    for cod, (lat, lon) in cent_subset.items():
        meta_c = cod_to_meta.get(cod, {})
        reg = meta_c.get("regione")
        prov = meta_c.get("sigla_provincia")
        if reg:
            b = bbox_reg.setdefault(reg, [lat, lat, lon, lon])
            b[0] = min(b[0], lat); b[1] = max(b[1], lat)
            b[2] = min(b[2], lon); b[3] = max(b[3], lon)
            if prov:
                reg_province.setdefault(reg, set()).add(prov)
        if prov:
            b = bbox_prov.setdefault(prov, [lat, lat, lon, lon])
            b[0] = min(b[0], lat); b[1] = max(b[1], lat)
            b[2] = min(b[2], lon); b[3] = max(b[3], lon)
    payload["meta"]["bbox_regioni"] = {k: [round(x, 4) for x in v] for k, v in bbox_reg.items()}
    payload["meta"]["bbox_province"] = {k: [round(x, 4) for x in v] for k, v in bbox_prov.items()}
    payload["meta"]["regione_province"] = {k: sorted(v) for k, v in reg_province.items()}

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

    # === PAYLOAD COMPATTO ===
    # Altervista NON gzippa i .json: il payload va minificato strutturalmente.
    # Schema colonnare + decimali tagliati + split in due file:
    #   comuni-essential.json: meta + top 300 comuni per popolazione (~80 KB)
    #     → caricato all'init, sufficiente per la prima vista
    #   comuni-full.json: TUTTI i 7896 comuni (~700 KB compatto)
    #     → fetch lazy quando l'utente seleziona regione/scope all/comune
    #       fuori dai top 300

    SOGLIA_ESSENTIAL = 300  # n comuni nel payload essential (per popolazione)

    # Schema colonnare: cols header + rows array di valori
    cols = [
        "codice_istat", "comune", "sigla_provincia", "regione",
        "n_contribuenti", "reddito_mediana", "reddito_p10", "reddito_p90",
        "ratio_p90_p10", "pct_sotto_15k", "pct_sopra_55k",
        "prezzo_acq_eur_mq_med", "affitto_eur_mq_mese_med",
        "rs_single_affitto", "rs_coppia_affitto", "rs_coppia_2f_affitto",
        "residuo_single_affitto", "residuo_coppia_2f_affitto",
        "indice_qualita", "omi_disponibile",
        "score_bes", "tasso_delitti_per_10k",
        "lat", "lon",
    ]

    def round_val(v, dec=1):
        if v is None or v == "":
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, float):
            return round(v, dec)
        return v

    def comune_to_row(c, lat_lon):
        return [
            c.get("codice_istat"),
            c.get("comune"),
            c.get("sigla_provincia"),
            c.get("regione"),
            int(c["n_contribuenti"]) if c.get("n_contribuenti") is not None else None,
            round_val(c.get("reddito_mediana"), 0),
            round_val(c.get("reddito_p10"), 0),
            round_val(c.get("reddito_p90"), 0),
            round_val(c.get("ratio_p90_p10"), 1),
            round_val(c.get("pct_sotto_15k"), 1),
            round_val(c.get("pct_sopra_55k"), 1),
            round_val(c.get("prezzo_acq_eur_mq_med"), 0),
            round_val(c.get("affitto_eur_mq_mese_med"), 1),
            round_val(c.get("rs_single_affitto"), 0),
            round_val(c.get("rs_coppia_affitto"), 0),
            round_val(c.get("rs_coppia_2f_affitto"), 0),
            round_val(c.get("residuo_single_affitto"), 0),
            round_val(c.get("residuo_coppia_2f_affitto"), 0),
            round_val(c.get("indice_qualita"), 1),
            bool(c.get("omi_disponibile")),
            round_val(c.get("score_bes"), 1),
            round_val(c.get("tasso_delitti_per_10k"), 1),
            lat_lon[0] if lat_lon else None,
            lat_lon[1] if lat_lon else None,
        ]

    # Sort per popolazione decrescente per priorità essential
    comuni_sorted = sorted(payload["comuni"],
                           key=lambda c: c.get("n_contribuenti") or 0, reverse=True)

    rows_full = [comune_to_row(c, cent_subset.get(c["codice_istat"])) for c in comuni_sorted]
    rows_essential = rows_full[:SOGLIA_ESSENTIAL]

    # Meta condivisa (no comuni, no centroidi inline → integrati in rows)
    meta_compact = {**payload["meta"]}
    # Lascia i bbox e regione_province (utili per cascata e zoom)

    essential = {
        "meta": meta_compact,
        "cols": cols,
        "rows": rows_essential,
        "total": len(rows_full),
        "essential_count": len(rows_essential),
    }
    full = {"cols": cols, "rows": rows_full}

    out_essential = OUT_DATA / "comuni-essential.json"
    out_essential.write_text(json.dumps(essential, ensure_ascii=False, separators=(",", ":")))
    log.info("scritto %s (%.1f KB, %d comuni)",
             out_essential, out_essential.stat().st_size / 1024, len(rows_essential))

    out_full = OUT_DATA / "comuni-full.json"
    out_full.write_text(json.dumps(full, ensure_ascii=False, separators=(",", ":")))
    log.info("scritto %s (%.1f KB, %d comuni)",
             out_full, out_full.stat().st_size / 1024, len(rows_full))

    # Template WP "page-macro-dove-vivere.php" allineato al pattern UB:
    # body con classi Tailwind (bg-neutral-50, font-sans), container glass,
    # sharebar, sezioni mt-8 rounded-3xl. Carica il JSON dashboard via fetch
    # da /wp-content/uploads/qualita-vita/comuni-dashboard.json (caricato via FTP).
    page_macro = build_wp_page_template(payload, anno, sem, n_comuni)
    wp_dir = ROOT / "wp-integration"
    wp_dir.mkdir(exist_ok=True)
    out_page = wp_dir / "page-macro-dove-vivere.php"
    out_page.write_text(page_macro, encoding="utf-8")
    log.info("scritto %s (%.1f KB)", out_page, out_page.stat().st_size / 1024)
    return 0


def build_wp_page_template(payload: dict, anno, sem, n_comuni: int) -> str:
    """Genera il template PHP page-macro-dove-vivere.php aderente al pattern
    di page-macro-redditi-italiani.php del tema landing-consulenza:
    Tailwind classes, body bg-neutral-50, container glass shadow-float,
    sezioni mt-8 rounded-3xl, sharebar standard, lc_render_nav_bar,
    partials/newsletter-substack, partials/footer-condividi.
    Il payload dati e' caricato via fetch async dal CDN sito (uploads dir)."""
    title = "Dove si vive bene in Italia: indice qualità della vita per comune"
    desc = (f"Reddito mediano (MEF), prezzi case e affitti OMI, costo della vita stimato per "
            f"{n_comuni}+ comuni italiani. Mappa interattiva, classifica top/bottom 30, "
            f"calcolatore reddito sostenibile e simulatore IRPEF ordinario vs flat tax. "
            f"Dati MEF {anno} + OMI semestre {sem}.")
    slug = "dove-vivere"
    return f"""<?php
/*
Template Name: Macro Dove vivere
Template Post Type: page
*/
if (!defined('ABSPATH')) exit;

global $lc_custom_og;
$lc_custom_og = true;
$_lang = lc_get_lang();

$site_name  = get_bloginfo('name');
$page_title = lc__({title!r});
$full_title = $page_title . ' | ' . $site_name;

$desc = lc__({desc!r});

$permalink = home_url('/macro/{slug}/');
$canonical = $permalink;

$og_image = function_exists('lc_og_image_url')
  ? lc_og_image_url($page_title, '{slug}')
  : '';

$share_url   = $permalink;
$share_title = $page_title;
$fb  = 'https://www.facebook.com/sharer/sharer.php?u=' . rawurlencode($share_url);
$x   = 'https://twitter.com/intent/tweet?url=' . rawurlencode($share_url) . '&text=' . rawurlencode($share_title);
$ln  = 'https://www.linkedin.com/sharing/share-offsite/?url=' . rawurlencode($share_url);
$pin = 'https://pinterest.com/pin/create/button/?url=' . rawurlencode($share_url) . '&description=' . rawurlencode($share_title);
if ($og_image) {{ $pin .= '&media=' . rawurlencode($og_image); }}

// Path JSON dashboard caricato via FTP nel folder uploads
$payload_url = content_url('/uploads/qualita-vita/comuni-essential.json');
$payload_full_url = content_url('/uploads/qualita-vita/comuni-full.json');
?><!doctype html>
<html lang="<?php echo $_lang; ?>">
<head>
  <meta charset="<?php bloginfo('charset'); ?>" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />

  <?php lc_seo_head([
    'title'       => $page_title,
    'description' => $desc,
    'url'         => $permalink,
    'image'       => $og_image,
    'type'        => 'article',
    'slug'        => '{slug}',
  ]); ?>
  <link rel="preconnect" href="https://cdn.plot.ly" crossorigin>
  <link rel="preload" as="fetch" href="<?php echo esc_url($payload_url); ?>" crossorigin>
  <?php include __DIR__ . '/partials/mobile-dashboard-helpers.php'; ?>

  <?php wp_head(); ?>

  <?php echo lc_jsonld_breadcrumbs([
    'Home'                          => home_url('/'),
    lc__('Analisi macroeconomiche') => home_url('/macro/'),
    $page_title                     => $permalink,
  ]); ?>
</head>

<body <?php body_class('bg-neutral-50 text-neutral-900 font-sans'); ?>>
<?php wp_body_open(); ?>

<div class="min-h-screen bg-grid">
  <div class="pointer-events-none fixed inset-0">
    <div class="absolute -top-24 -left-24 h-96 w-96 rounded-full blur-3xl opacity-40" style="background:#F17820;"></div>
    <div class="absolute top-24 -right-24 h-[28rem] w-[28rem] rounded-full blur-3xl opacity-30" style="background:#00355F;"></div>
    <div class="absolute bottom-[-140px] left-1/2 -translate-x-1/2 h-[34rem] w-[34rem] rounded-full blur-3xl opacity-20" style="background:#1b7f3a;"></div>
  </div>

  <div class="relative">
    <main class="mx-auto max-w-6xl px-6 pt-12 pb-32">

      <!-- HERO -->
      <div class="glass shadow-float rounded-3xl p-8 md:p-10">
        <?php if (function_exists('lc_render_nav_bar')) lc_render_nav_bar(); ?>

        <div class="mt-6 text-xs text-slate-600 flex items-center gap-2">
          <time datetime="2026-04-29"><?php lc_e('29 Aprile 2026'); ?></time>
          <span class="opacity-40">&bull;</span>
          <span>Umberto Bertonelli</span>
        </div>

        <h1 class="mt-3 text-3xl md:text-5xl font-semibold tracking-tight text-slate-950"><?php echo esc_html($page_title); ?></h1>

        <p class="mt-4 text-lg text-slate-700 max-w-3xl">
          <?php lc_e('Reddito mediano dichiarato (MEF), affitti e prezzi al mq (Agenzia Entrate OMI), reddito sostenibile per famiglia tipo, simulatore IRPEF vs flat tax. Indice qualità della vita per ogni comune con dati disponibili.'); ?>
        </p>

        <div class="sharebar mt-4">
          <div class="sharebar-left"><?php lc_e('Condividi'); ?></div>
          <div class="sharebar-right">
            <a class="share-btn" href="<?php echo esc_url($fb); ?>" target="_blank" rel="noopener" aria-label="Facebook"><i class="fa-brands fa-facebook-f" aria-hidden="true"></i></a>
            <a class="share-btn" href="<?php echo esc_url($x); ?>" target="_blank" rel="noopener" aria-label="X"><i class="fa-brands fa-x-twitter" aria-hidden="true"></i></a>
            <a class="share-btn" href="<?php echo esc_url($ln); ?>" target="_blank" rel="noopener" aria-label="LinkedIn"><i class="fa-brands fa-linkedin-in" aria-hidden="true"></i></a>
            <a class="share-btn" href="<?php echo esc_url($pin); ?>" target="_blank" rel="noopener" aria-label="Pinterest"><i class="fa-brands fa-pinterest-p" aria-hidden="true"></i></a>
          </div>
        </div>

        <div id="qvi-kpi" class="mt-8 grid sm:grid-cols-2 lg:grid-cols-4 gap-4"></div>
      </div>

      <?php if (file_exists(__DIR__ . '/partials/newsletter-substack.php')) include __DIR__ . '/partials/newsletter-substack.php'; ?>

      <!-- 1. MAPPA -->
      <section class="mt-8 glass shadow-float rounded-3xl p-8 md:p-10">
        <h2 class="text-2xl md:text-3xl font-semibold tracking-tight text-slate-950"><?php lc_e('1. Mappa interattiva: dove si vive meglio'); ?></h2>
        <div class="mt-6 prose prose-slate max-w-none text-slate-700 leading-relaxed">
          <p><?php lc_e('Ogni cerchio è un comune. Dimensione: numero di contribuenti. Colore: indicatore selezionato. Click su un punto per il dettaglio.'); ?></p>
          <p class="text-sm rounded-xl bg-slate-50 border border-slate-200 p-3"><?php lc_e('La mappa carica di default i <strong>capoluoghi e città grandi</strong> (per leggerezza). Filtra una regione, oppure clicca <em>Mostra tutti i comuni</em> per il rendering completo (~7.900 punti).'); ?></p>
        </div>
        <div class="mt-6 flex flex-wrap gap-4 items-center">
          <label class="text-sm"><?php lc_e('Indicatore'); ?>
            <select id="qvi-indicatore" class="ml-2 rounded border border-slate-300 px-2 py-1 text-sm">
              <option value="indice_qualita" selected>Indice qualità (composito)</option>
              <option value="reddito_mediana">Reddito mediano</option>
              <option value="affitto_eur_mq_mese_med">Affitto € /mq mese</option>
              <option value="prezzo_acq_eur_mq_med">Prezzo acquisto € /mq</option>
              <option value="ratio_p90_p10">Disuguaglianza P90/P10</option>
              <option value="residuo_single_affitto">Residuo netto annuo (single)</option>
              <option value="score_bes">BES regionale (servizi/istruzione/lavoro)</option>
              <option value="tasso_delitti_per_10k">Delitti per 10k abitanti</option>
            </select>
          </label>
          <label class="text-sm"><?php lc_e('Regione'); ?>
            <select id="qvi-regione" class="ml-2 rounded border border-slate-300 px-2 py-1 text-sm">
              <option value=""><?php lc_e('Tutte'); ?></option>
            </select>
          </label>
          <label class="text-sm"><?php lc_e('Provincia'); ?>
            <select id="qvi-provincia" class="ml-2 rounded border border-slate-300 px-2 py-1 text-sm" disabled>
              <option value=""><?php lc_e('Tutte'); ?></option>
            </select>
          </label>
          <label class="text-sm"><?php lc_e('Visualizza'); ?>
            <select id="qvi-scope" class="ml-2 rounded border border-slate-300 px-2 py-1 text-sm">
              <option value="big" selected>Capoluoghi e città grandi (≥40k contribuenti)</option>
              <option value="all">Tutti i comuni</option>
            </select>
          </label>
        </div>
        <div id="qvi-map" class="mt-6 rounded-2xl overflow-hidden" style="min-height:560px;"></div>
        <p class="mt-4 text-sm text-slate-500"><?php lc_e('Dati: MEF dichiarazioni IRPEF + Agenzia Entrate OMI. Indice qualità: 60% residuo netto annuo + 40% accessibilità casa. Estendibile con criminalità e servizi BES quando i dati ISTAT torneranno raggiungibili.'); ?></p>
      </section>

      <!-- 2. CLASSIFICA -->
      <section class="mt-8 glass shadow-float rounded-3xl p-8 md:p-10">
        <h2 class="text-2xl md:text-3xl font-semibold tracking-tight text-slate-950"><?php lc_e('2. Classifica: top e bottom 30 per indice qualità'); ?></h2>
        <div class="mt-6 grid md:grid-cols-2 gap-6">
          <div>
            <h3 class="text-lg font-semibold text-slate-900 mb-2"><?php lc_e('Top 30: vivibilità più alta'); ?></h3>
            <div class="overflow-x-auto"><table id="qvi-top" class="w-full text-sm">
              <thead class="bg-slate-100 text-slate-700"><tr>
                <th class="text-left px-2 py-1">#</th><th class="text-left px-2 py-1"><?php lc_e('Comune'); ?></th>
                <th class="text-left px-2 py-1">Pr</th><th class="text-right px-2 py-1"><?php lc_e('Indice'); ?></th>
                <th class="text-right px-2 py-1"><?php lc_e('Mediana €'); ?></th>
                <th class="text-right px-2 py-1"><?php lc_e('Affitto €/mq'); ?></th>
              </tr></thead>
              <tbody></tbody>
            </table></div>
          </div>
          <div>
            <h3 class="text-lg font-semibold text-slate-900 mb-2"><?php lc_e('Bottom 30: vivibilità più bassa'); ?></h3>
            <div class="overflow-x-auto"><table id="qvi-bot" class="w-full text-sm">
              <thead class="bg-slate-100 text-slate-700"><tr>
                <th class="text-left px-2 py-1">#</th><th class="text-left px-2 py-1"><?php lc_e('Comune'); ?></th>
                <th class="text-left px-2 py-1">Pr</th><th class="text-right px-2 py-1"><?php lc_e('Indice'); ?></th>
                <th class="text-right px-2 py-1"><?php lc_e('Mediana €'); ?></th>
                <th class="text-right px-2 py-1"><?php lc_e('Affitto €/mq'); ?></th>
              </tr></thead>
              <tbody></tbody>
            </table></div>
          </div>
        </div>
      </section>

      <!-- 3. CALCOLATORE -->
      <section class="mt-8 glass shadow-float rounded-3xl p-8 md:p-10">
        <h2 class="text-2xl md:text-3xl font-semibold tracking-tight text-slate-950"><?php lc_e('3. Calcolatore reddito sostenibile'); ?></h2>
        <div class="mt-6 prose prose-slate max-w-none text-slate-700 leading-relaxed">
          <p><?php lc_e('Quanto reddito lordo familiare serve per coprire le spese in un comune scelto? Combina paniere ISTAT non-casa stimato per profilo + costo casa OMI + IRPEF/addizionali stimati.'); ?></p>
        </div>
        <div class="mt-6 grid md:grid-cols-3 gap-4">
          <label class="text-sm"><?php lc_e('Regione'); ?>
            <select id="qvi-calc-regione" class="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm">
              <option value=""><?php lc_e('Seleziona regione…'); ?></option>
            </select>
          </label>
          <label class="text-sm"><?php lc_e('Provincia'); ?>
            <select id="qvi-calc-provincia" class="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm" disabled>
              <option value=""><?php lc_e('Prima la regione'); ?></option>
            </select>
          </label>
          <label class="text-sm"><?php lc_e('Comune'); ?>
            <select id="qvi-calc-comune" class="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm" disabled>
              <option value=""><?php lc_e('Prima la provincia'); ?></option>
            </select>
          </label>
        </div>
        <div class="mt-3 grid md:grid-cols-2 gap-4">
          <label class="text-sm"><?php lc_e('Profilo familiare'); ?>
            <select id="qvi-calc-profilo" class="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm">
              <option value="single">1 adulto</option>
              <option value="coppia" selected>Coppia, 1 stipendio</option>
              <option value="coppia2">Coppia, 2 stipendi</option>
              <option value="coppia_1f">Coppia + 1 figlio, 1 stip.</option>
              <option value="coppia_2f">Coppia + 2 figli, 2 stip.</option>
            </select>
          </label>
          <label class="text-sm"><?php lc_e('Modalità casa'); ?>
            <select id="qvi-calc-modalita" class="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm">
              <option value="affitto" selected>Affitto</option>
              <option value="proprieta">Già proprietario</option>
            </select>
          </label>
        </div>
        <div id="qvi-calc-out" class="mt-4 rounded-xl bg-slate-50 border border-slate-200 p-4 text-sm text-slate-700"><?php lc_e('Seleziona un comune per vedere il calcolo.'); ?></div>
      </section>

      <!-- 4. SIMULATORE IRPEF VS FLAT TAX -->
      <section class="mt-8 glass shadow-float rounded-3xl p-8 md:p-10">
        <h2 class="text-2xl md:text-3xl font-semibold tracking-tight text-slate-950"><?php lc_e('4. Simula il tuo reddito: IRPEF ordinario vs flat tax'); ?></h2>
        <div class="mt-6 prose prose-slate max-w-none text-slate-700 leading-relaxed">
          <p><?php lc_e('Inserisci un reddito imponibile (lordo, già al netto dei contributi previdenziali). Confronta il netto effettivo nei tre regimi e quanto residua dalla spesa totale annua per il comune scelto sopra.'); ?></p>
          <p class="text-sm rounded-xl bg-slate-50 border border-slate-200 p-3"><?php lc_e('Il regime forfettario richiede ricavi entro <strong>85.000 €/anno</strong>: oltre questa soglia non è applicabile (resta solo IRPEF ordinario).'); ?></p>
        </div>
        <div class="mt-6 grid md:grid-cols-2 gap-4">
          <label class="text-sm"><?php lc_e('Reddito imponibile annuo (€)'); ?>
            <input type="number" id="qvi-sim-reddito" min="0" max="500000" step="1000" value="30000"
                   class="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm">
          </label>
          <label class="text-sm"><?php lc_e('Regime'); ?>
            <select id="qvi-sim-regime" class="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm">
              <option value="ordinario" selected>IRPEF ordinario (dipendente)</option>
              <option value="flat15">Flat tax 15% (forfettario)</option>
              <option value="flat5">Flat tax 5% (forfettario startup)</option>
            </select>
          </label>
        </div>
        <div id="qvi-sim-out" class="mt-4 rounded-xl bg-blue-50 border border-blue-200 p-4 text-sm text-slate-800"><?php lc_e('Inserisci un reddito per simulare.'); ?></div>
      </section>

      <!-- 5. DETTAGLIO COMUNE -->
      <section id="qvi-dettaglio" class="mt-8 glass shadow-float rounded-3xl p-8 md:p-10">
        <h2 class="text-2xl md:text-3xl font-semibold tracking-tight text-slate-950"><?php lc_e('5. Dettaglio comune'); ?></h2>
        <div id="qvi-dettaglio-body" class="mt-6 text-sm text-slate-700"><?php lc_e('Clicca un punto sulla mappa o un comune in classifica per vedere i dettagli.'); ?></div>
      </section>

      <!-- 6. METODOLOGIA INDICE QUALITÀ -->
      <section class="mt-8 glass shadow-float rounded-3xl p-8 md:p-10">
        <h2 class="text-2xl md:text-3xl font-semibold tracking-tight text-slate-950"><?php lc_e('6. Come si calcola l\u2019indice qualità'); ?></h2>
        <div class="mt-6 prose prose-slate max-w-none text-slate-700 leading-relaxed">
          <p><?php lc_e("L\u2019indice è un punteggio 0-100 che combina cinque dimensioni: <strong>residuo netto disponibile</strong> dopo costo casa, <strong>accessibilità del costo casa</strong>, <strong>servizi BES regionali</strong> (salute, istruzione, lavoro, banda larga), <strong>sicurezza</strong> (tasso delitti capoluogo) e <strong>disuguaglianza interna</strong> (P90/P10). Pesi: 40% residuo + 20% accessibilità + 20% BES + 15% sicurezza + 5% disuguaglianza. Paniere non-casa modulato per IPC regionale ISTAT."); ?></p>

          <h3 class="text-lg font-semibold text-slate-900 mt-4"><?php lc_e('Formula attuale'); ?></h3>
          <pre style="background:#0f172a;color:#f1f5f9;padding:1rem 1.25rem;border-radius:0.5rem;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:0.85rem;line-height:1.55;overflow-x:auto;white-space:pre;margin:0.75rem 0;"><span style="color:#fbbf24">residuo_netto</span> = mediana_netta &minus; costo_vita_minimo_single
              = nettoDaLordo(reddito_mediana_comune)
              &minus; affitto_OMI &times; 50 mq &times; 12 mesi
              &minus; paniere_ISTAT_single &times; IPC_regionale (8.400 &euro; &times; 0,90&minus;1,10)

<span style="color:#fbbf24">score_residuo</span>  = normalize(residuo_netto, 5&deg;-95&deg; percentile, 0-100)
<span style="color:#fbbf24">score_casa</span>     = 100 &minus; normalize(prezzo_acquisto_OMI, 5&deg;-95&deg; percentile)
<span style="color:#fbbf24">score_disug</span>    = 100 &minus; normalize(P90 / P10, 5&deg;-95&deg; percentile)
<span style="color:#fbbf24">score_bes</span>      = normalize(media z-score 8 indicatori BES regionali)
<span style="color:#fbbf24">score_sic</span>      = 100 &minus; normalize(delitti per 10k abitanti, capoluogo)

<span style="color:#34d399;font-weight:600">indice_qualita = 0,40 &times; score_residuo + 0,20 &times; score_casa
              + 0,20 &times; score_bes + 0,15 &times; score_sic + 0,05 &times; score_disug</span></pre>

          <h3 class="text-lg font-semibold text-slate-900 mt-4"><?php lc_e('Peculiarità e scelte metodologiche'); ?></h3>
          <ul class="mt-2 space-y-1.5 text-sm">
            <li><strong>Mediana, non media</strong>: la media è gonfiata da pochi redditi alti. La mediana stimata per interpolazione lineare nelle 8 fasce MEF è il «vero centro» della distribuzione comunale.</li>
            <li><strong>Reddito netto stimato</strong>: applichiamo IRPEF 2025 a scaglioni (23/35/43%), detrazione dipendente, addizionali regionale 1,73% e comunale 0,5% medie. Approssimazione: in dashboard l\u2019indice ipotizza profilo single senza figli.</li>
            <li><strong>Costo casa minimo single</strong>: 50 mq × affitto OMI medio comunale × 12 mesi. È il costo casa da affittuario per una persona che vive sola.</li>
            <li><strong>Paniere non-casa modulato per IPC regionale</strong>: stima ISTAT 2023 spese famiglie escluso voce abitazione (8.400 €/anno per single), moltiplicata per l\u2019indice prezzi al consumo regionale (NIC base 2015): 0,90 Calabria, 0,91 Sicilia, 1,00 Lazio, 1,03 Veneto, 1,05 Lombardia, 1,10 Trentino-Alto Adige.</li>
            <li><strong>Normalizzazione robusta</strong>: usiamo 5° e 95° percentile come bounds (non min/max), per non far dominare gli outlier. I comuni più ricchi/poveri saturano a 100 o 0.</li>
            <li><strong>Inverte il segno sulla casa</strong>: prezzo acquisto basso = score alto (accessibilità). Riccione e Milano vanno in fondo perché il costo casa erode il residuo.</li>
            <li><strong>Servizi BES (regionale)</strong>: media z-score di 8 indicatori ISTAT 2024: speranza di vita, istruzione secondaria/terziaria/NEET, occupazione/non-partecipazione, affluenza elettorale, banda larga. Verso "+" o "-" per ogni indicatore (es. NEET basso = meglio). Score normalizzato 0-100.</li>
            <li><strong>Sicurezza (capoluoghi)</strong>: tasso totale delitti per 10k abitanti del comune capoluogo, anno 2024. Aggrega 55 tipologie ISTAT (omicidi, furti, rapine, violenze, cybercrime). Applicato come proxy a tutti i comuni della provincia. Stato di base: i grandi capoluoghi hanno tassi maggiori (Milano, Roma, Napoli).</li>
            <li><strong>Comuni rumorosi</strong>: con &lt; 500 contribuenti la mediana è instabile. Sono nel dropdown ma flaggati nel dettaglio.</li>
          </ul>

          <h3 class="text-lg font-semibold text-slate-900 mt-4"><?php lc_e("Cosa NON misura ancora"); ?></h3>
          <ul class="mt-2 space-y-1.5 text-sm">
            <li><strong>Granularita BES</strong>: il dato BES e' regionale (NUTS-2), non provinciale ne comunale. Tutti i comuni della stessa regione condividono lo stesso score BES. Per granularita maggiore servono indici sintetici provinciali (ISTAT li produce ogni 2-3 anni come pubblicazione separata).</li>
            <li><strong>Granularita delitti</strong>: il dato di delittuosita ISTAT 2024 e' del comune capoluogo della provincia (242 capoluoghi/grandi citta), applicato a tutti i comuni della provincia (proxy provinciale). I comuni piccoli hanno tipicamente tassi piu bassi del capoluogo.</li>
            <li><strong>Redditi a tassazione separata e patrimonio</strong>: la dichiarazione IRPEF MEF non include cedolare secca affitti (21%), interessi, dividendi e plusvalenze (26%), vincite e premi. Il patrimonio (case, depositi, titoli) non entra in dichiarazione IRPEF. La mediana del reddito complessivo è quindi una proxy per la <em>capacità di spesa corrente</em>, non per la ricchezza. Nei comuni a forte presenza di seconde case e percettori di rendita (Cortina, Capalbio, Forte dei Marmi, Portofino) il reddito disponibile reale dei residenti è plausibilmente più alto di quello fotografato qui.</li>
          </ul>

          <p class="text-sm text-slate-600 mt-4"><?php lc_e("L\u2019indice è uno strumento descrittivo, non normativo. Non risponde a «dove devo trasferirmi» ma a «dove un reddito mediano si traduce in più residuo dopo il costo casa». Per scelte concrete pesare le proprie priorità: clima, lavoro, famiglia, qualità servizi."); ?></p>
        </div>
      </section>

      <!-- FONTI -->
      <section class="mt-8 glass shadow-float rounded-3xl p-8 md:p-10">
        <h2 class="text-2xl md:text-3xl font-semibold tracking-tight text-slate-950"><?php lc_e('Fonti e codice'); ?></h2>
        <ul class="mt-4 space-y-2 text-sm text-slate-700">
          <li><strong>MEF Dipartimento delle Finanze</strong>: <a href="https://www1.finanze.gov.it/finanze/analisi_stat/public/v_4_0_0/contenuti/" target="_blank" rel="noopener">dichiarazioni IRPEF su base comunale</a>, anno {anno}.</li>
          <li><strong>Agenzia delle Entrate OMI</strong>: <a href="https://wwwt.agenziaentrate.gov.it/geopoi_omi/index.htm" target="_blank" rel="noopener">quotazioni immobiliari</a>, semestre {sem}.</li>
          <li><strong>ISTAT</strong>: <a href="https://www.istat.it/it/files/2024/01/Stat-today_n2_2024.pdf" target="_blank" rel="noopener">consumi delle famiglie 2023</a> (paniere non-casa per profilo).</li>
          <li><strong>OpenPolis</strong>: <a href="https://github.com/openpolis/geojson-italy" target="_blank" rel="noopener">geometrie comuni</a> per centroidi mappa.</li>
          <li><strong>Codice sorgente</strong>: <a href="https://github.com/DrElegantia/qualita-vita-italia" target="_blank" rel="noopener">github.com/DrElegantia/qualita-vita-italia</a> (MIT). Pipeline auto-aggiornante mensile, dati MEF e OMI scaricati ai rispettivi semestri/anni di pubblicazione.</li>
        </ul>
      </section>

      <?php if (file_exists(__DIR__ . '/partials/footer-condividi.php')) include __DIR__ . '/partials/footer-condividi.php'; ?>

    </main>
  </div>
</div>

<script>
(function() {{
  const PAYLOAD_URL = <?php echo wp_json_encode($payload_url); ?>;
  const PAYLOAD_FULL_URL = <?php echo wp_json_encode($payload_full_url); ?>;
  const PLOTLY_URL = "https://cdn.plot.ly/plotly-2.27.0.min.js";
  const PALETTE = {{ orange: "#F17820", blue: "#00355F", green: "#1b7f3a", red: "#c0392b", neutral: "#475569" }};

  // === Lazy loader Plotly ===
  let _plotlyPromise = null;
  function loadPlotly() {{
    if (window.Plotly) return Promise.resolve(window.Plotly);
    if (_plotlyPromise) return _plotlyPromise;
    _plotlyPromise = new Promise((resolve, reject) => {{
      const s = document.createElement("script");
      s.src = PLOTLY_URL; s.async = true;
      s.onload = () => resolve(window.Plotly);
      s.onerror = reject;
      document.head.appendChild(s);
    }});
    return _plotlyPromise;
  }}

  // === Schema colonnare → array of objects ===
  function rowsToObjects(cols, rows) {{
    return rows.map(r => {{
      const o = {{}};
      for (let i = 0; i < cols.length; i++) o[cols[i]] = r[i];
      return o;
    }});
  }}

  // === Lazy loader full payload ===
  let _fullPromise = null;
  function loadFull() {{
    if (_fullPromise) return _fullPromise;
    _fullPromise = fetch(PAYLOAD_FULL_URL).then(r => r.json()).then(j => {{
      const objs = rowsToObjects(j.cols, j.rows);
      const cent = {{}};
      for (const c of objs) {{
        if (c.lat != null && c.lon != null) cent[c.codice_istat] = [c.lat, c.lon];
      }}
      return {{ comuni: objs, centroidi: cent }};
    }});
    return _fullPromise;
  }}

  // IRPEF 2025 + flat tax (clientside)
  const SCAGLIONI = [[28000, 0.23], [50000, 0.35], [Infinity, 0.43]];
  const ADD_REG = 0.0173, ADD_COM = 0.005, DETR_BASE = 1955, DETR_FIG = 950;
  const FLAT_LIMIT = 85000;
  function irpefLorda(r) {{ let imp=0,prev=0; for(const[s,a] of SCAGLIONI){{ if(r<=prev)break; const cap=Math.min(r,s); imp+=(cap-prev)*a; prev=cap; }} return imp; }}
  function detrazioneDip(r) {{ if(r<=15000)return DETR_BASE; if(r<=28000)return 1910+1190*(28000-r)/13000; if(r<=50000)return 1910*(50000-r)/22000; return 0; }}
  function nettoDaLordo(lordo,nFigli) {{ const ip=Math.max(0,irpefLorda(lordo)-detrazioneDip(lordo)-nFigli*DETR_FIG); const add=lordo*(ADD_REG+ADD_COM); return lordo-ip-add; }}
  function nettoFlatTax(lordo,al) {{ if(lordo>FLAT_LIMIT)return null; return lordo*(1-al); }}
  function lordoPerNetto(target,nFigli,nP) {{ if(target<=0)return 0; let lo=0,hi=250000; const tp=target/nP; for(let i=0;i<40;i++){{ const m=(lo+hi)/2; if(nettoDaLordo(m,nFigli)<tp)lo=m; else hi=m; }} return((lo+hi)/2)*nP; }}
  function costoCasaAnnuo(p,m,af,ac) {{ if(m==="affitto")return af?p.mq*af*12:null; if(m==="proprieta")return 1500; return null; }}
  // Paniere modulato per IPC regionale (mult ~0.90-1.10)
  function paniereReg(profKey, regione, panieri, ipc) {{
    const base = panieri[profKey] || 0;
    const mult = (ipc && regione && ipc[regione]) ? ipc[regione] : 1.0;
    return base * mult;
  }}
  function fmt(n) {{ if(n==null||isNaN(n))return "—"; return Math.round(n).toLocaleString("it-IT")+" €"; }}
  function fmtN(n,d=2) {{ if(n==null||isNaN(n))return "—"; return n.toFixed(d); }}

  let DATA = null;
  let FULL_LOADED = false;

  function init(payloadRaw) {{
    DATA = payloadRaw;
    let COMUNI = rowsToObjects(payloadRaw.cols, payloadRaw.rows);
    let CENTROIDI = {{}};
    for (const c of COMUNI) {{
      if (c.lat != null && c.lon != null) CENTROIDI[c.codice_istat] = [c.lat, c.lon];
    }}
    const totalComuni = payloadRaw.total || COMUNI.length;
    const payload = payloadRaw;
    const PROFILI = payload.meta.profili;
    const PANIERE = payload.meta.paniere_non_casa;

    // Espansione lazy: scarica TUTTI i comuni e merge.
    // Triggered da: scope=all, selezione regione, popolaProvince calcolatore.
    function ensureFull() {{
      if (FULL_LOADED) return Promise.resolve();
      return loadFull().then(({{comuni, centroidi}}) => {{
        COMUNI = comuni;
        CENTROIDI = centroidi;
        FULL_LOADED = true;
        // Ripopola dropdown comune se gia inizializzato
        if (typeof regCalc !== "undefined" && regCalc.value) popolaComuni();
      }});
    }}
    const IPC = payload.meta.ipc_regionale || {{}};

    // KPI
    const kpiHtml = [
      ['<?php lc_e("Comuni totali"); ?>', COMUNI.length.toLocaleString("it-IT")],
      ['<?php lc_e("Comuni con OMI"); ?>', (payload.meta.n_comuni_con_omi||0).toLocaleString("it-IT")],
      ['<?php lc_e("Mediana naz. (€)"); ?>', fmt(payload.meta.mediana_naz || null)],
      ['<?php lc_e("Dati"); ?>', `MEF ${{payload.meta.anno_redditi||"—"}} · OMI ${{payload.meta.semestre_omi||"—"}}`],
    ].map(([k,v]) => `<div class="rounded-2xl bg-white/70 backdrop-blur border border-white/60 p-4 shadow-sm"><div class="text-xs uppercase tracking-wide text-slate-500">${{k}}</div><div class="mt-1 text-xl font-semibold text-slate-900">${{v}}</div></div>`).join("");
    document.getElementById("qvi-kpi").innerHTML = kpiHtml;

    // Regioni + provincia mappa (cascata)
    const regioni = [...new Set(COMUNI.map(c=>c.regione).filter(Boolean))].sort();
    const regSel = document.getElementById("qvi-regione");
    const provMapSel = document.getElementById("qvi-provincia");
    for(const r of regioni) {{ const o=document.createElement("option"); o.value=r; o.text=r; regSel.appendChild(o); }}
    function popolaProvinceMappa() {{
      provMapSel.innerHTML = '<option value="">Tutte</option>';
      const reg = regSel.value;
      if (!reg) {{ provMapSel.disabled = true; return; }}
      const provs = (payload.meta.regione_province||{{}})[reg]
                    || [...new Set(COMUNI.filter(c=>c.regione===reg).map(c=>c.sigla_provincia))].sort();
      provs.forEach(p => {{ const o=document.createElement("option"); o.value=p; o.text=p; provMapSel.appendChild(o); }});
      provMapSel.disabled = false;
    }}

    // Bbox → mapbox center+zoom
    function bboxToView(bbox) {{
      // bbox: [lat_min, lat_max, lon_min, lon_max]
      const [latMin, latMax, lonMin, lonMax] = bbox;
      const lat = (latMin + latMax) / 2;
      const lon = (lonMin + lonMax) / 2;
      const span = Math.max(latMax - latMin, (lonMax - lonMin) * 0.7);
      let zoom = 6;
      if (span < 0.5) zoom = 9.5;
      else if (span < 1) zoom = 8.5;
      else if (span < 2) zoom = 7.5;
      else if (span < 4) zoom = 7;
      else if (span < 8) zoom = 6.5;
      return {{ center: {{lat, lon}}, zoom }};
    }}

    // Cascata Regione → Provincia → Comune (alfabetico in ogni step)
    const regCalc = document.getElementById("qvi-calc-regione");
    const provCalc = document.getElementById("qvi-calc-provincia");
    const comCalc = document.getElementById("qvi-calc-comune");
    regioni.forEach(r => {{ const o=document.createElement("option"); o.value=r; o.text=r; regCalc.appendChild(o); }});
    function popolaProvince() {{
      const reg = regCalc.value;
      provCalc.innerHTML = "";
      comCalc.innerHTML = "";
      comCalc.disabled = true;
      if (!reg) {{
        provCalc.innerHTML = '<option value="">Prima la regione</option>';
        provCalc.disabled = true;
        comCalc.innerHTML = '<option value="">Prima la provincia</option>';
        return;
      }}
      const provs = [...new Set(COMUNI.filter(c => c.regione === reg).map(c => c.sigla_provincia))].sort();
      provCalc.innerHTML = '<option value="">Seleziona provincia…</option>';
      provs.forEach(p => {{ const o=document.createElement("option"); o.value=p; o.text=p; provCalc.appendChild(o); }});
      provCalc.disabled = false;
      comCalc.innerHTML = '<option value="">Prima la provincia</option>';
    }}
    function popolaComuni() {{
      const reg = regCalc.value, prov = provCalc.value;
      comCalc.innerHTML = "";
      if (!reg || !prov) {{
        comCalc.innerHTML = '<option value="">Prima la provincia</option>';
        comCalc.disabled = true;
        return;
      }}
      const subset = COMUNI.filter(c => c.regione === reg && c.sigla_provincia === prov)
                           .sort((a,b)=>a.comune.localeCompare(b.comune));
      comCalc.innerHTML = '<option value="">Seleziona comune…</option>';
      subset.forEach(c => {{
        const o=document.createElement("option");
        o.value=c.codice_istat;
        o.text = c.comune + (c.omi_disponibile ? "" : " · OMI non disponibile");
        comCalc.appendChild(o);
      }});
      comCalc.disabled = false;
    }}
    regCalc.addEventListener("change", () => {{ ensureFull().then(popolaProvince); }});
    provCalc.addEventListener("change", () => {{ popolaComuni(); }});

    const SOGLIA_BIG = 40000; // n_contribuenti per "città grande"
    function buildMap() {{
      const indic = document.getElementById("qvi-indicatore").value;
      const scope = document.getElementById("qvi-scope").value;
      const reg = regSel.value;
      const prov = provMapSel.value;
      // Filtri:
      // - se provincia selezionata → solo quella provincia (override scope)
      // - se regione selezionata (no provincia) → tutti i comuni di quella regione
      // - altrimenti applica scope (big = capoluoghi grandi, all = tutto)
      const subset = COMUNI.filter(c => {{
        if (!CENTROIDI[c.codice_istat]) return false;
        if (c[indic] == null) return false;
        if (prov) return c.sigla_provincia === prov;
        if (reg) return c.regione === reg;
        if (scope === "big") return c.n_contribuenti >= SOGLIA_BIG;
        return true;
      }});
      const z = subset.map(c => c[indic]);
      const text = subset.map(c =>
        `<b>${{c.comune}}</b> (${{c.sigla_provincia}})<br>` +
        `Mediana: ${{fmt(c.reddito_mediana)}}<br>` +
        `Affitto: ${{fmtN(c.affitto_eur_mq_mese_med)}} €/mq mese<br>` +
        `Prezzo acq: ${{fmt(c.prezzo_acq_eur_mq_med)}}/mq<br>` +
        `Indice: ${{fmtN(c.indice_qualita,1)}}/100`
      );
      const lats=subset.map(c=>CENTROIDI[c.codice_istat][0]);
      const lons=subset.map(c=>CENTROIDI[c.codice_istat][1]);
      const sizes=subset.map(c=>Math.max(8, Math.min(40, Math.log10(c.n_contribuenti||1000)*6)));

      // View: zoom auto su regione/provincia selezionata
      let view = {{ center: {{lat: 42.5, lon: 12.5}}, zoom: 5.2 }};
      if (prov && payload.meta.bbox_province && payload.meta.bbox_province[prov]) {{
        view = bboxToView(payload.meta.bbox_province[prov]);
      }} else if (reg && payload.meta.bbox_regioni && payload.meta.bbox_regioni[reg]) {{
        view = bboxToView(payload.meta.bbox_regioni[reg]);
      }}

      const trace = {{
        type:"scattermapbox", mode:"markers", lat:lats, lon:lons,
        marker: {{ size:sizes, color:z, colorscale: indic==="indice_qualita"||indic.startsWith("residuo")||indic==="reddito_mediana" ? "RdYlGn" : "RdYlGn_r", showscale:true, colorbar:{{title:indic, thickness:14, len:0.7}} }},
        text:text, hovertemplate:"%{{text}}<extra></extra>",
        customdata: subset.map(c=>c.codice_istat),
      }};
      const layout = {{ mapbox:{{style:"open-street-map", center:view.center, zoom:view.zoom}}, margin:{{t:10,b:10,l:10,r:10}}, height:560, paper_bgcolor:"rgba(0,0,0,0)" }};
      Plotly.newPlot("qvi-map", [trace], layout, {{displayModeBar:false, responsive:true}}).then(gd => {{
        gd.on("plotly_click", e => mostraDettaglio(e.points[0].customdata));
      }});
    }}
    // Render mappa solo dopo che Plotly e' caricato (lazy).
    // Per l'init mostriamo placeholder leggero finche' l'utente non scrolla
    // alla mappa. IntersectionObserver attiva il primo render.
    function ensurePlotlyAndBuild() {{
      const mapEl = document.getElementById("qvi-map");
      mapEl.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#64748b">Caricamento mappa…</div>';
      return loadPlotly().then(buildMap);
    }}
    let mapTriggered = false;
    function triggerMap() {{ if (mapTriggered) return; mapTriggered = true; ensurePlotlyAndBuild(); }}
    if ("IntersectionObserver" in window) {{
      const io = new IntersectionObserver((entries) => {{
        if (entries.some(e => e.isIntersecting)) {{ triggerMap(); io.disconnect(); }}
      }}, {{rootMargin: "200px"}});
      io.observe(document.getElementById("qvi-map"));
    }} else {{
      triggerMap();
    }}
    document.getElementById("qvi-indicatore").addEventListener("change", () => mapTriggered && buildMap());
    document.getElementById("qvi-regione").addEventListener("change", () => {{
      ensureFull().then(() => {{ popolaProvinceMappa(); if (mapTriggered) buildMap(); }});
    }});
    document.getElementById("qvi-provincia").addEventListener("change", () => mapTriggered && buildMap());
    document.getElementById("qvi-scope").addEventListener("change", (e) => {{
      if (e.target.value === "all") ensureFull().then(() => mapTriggered && buildMap());
      else if (mapTriggered) buildMap();
    }});

    // Tabelle
    const sortedDesc = [...COMUNI].filter(c=>c.indice_qualita!=null).sort((a,b)=>b.indice_qualita-a.indice_qualita);
    function tabella(arr, sel) {{
      const tb = document.querySelector(sel+" tbody"); tb.innerHTML="";
      arr.forEach((c,i) => {{
        const tr=document.createElement("tr"); tr.className="border-t border-slate-200 cursor-pointer hover:bg-slate-50";
        tr.innerHTML = `<td class="px-2 py-1.5">${{i+1}}</td><td class="px-2 py-1.5">${{c.comune}}</td><td class="px-2 py-1.5">${{c.sigla_provincia}}</td>` +
          `<td class="px-2 py-1.5 text-right font-medium">${{fmtN(c.indice_qualita,1)}}</td>` +
          `<td class="px-2 py-1.5 text-right">${{fmt(c.reddito_mediana)}}</td>` +
          `<td class="px-2 py-1.5 text-right">${{fmtN(c.affitto_eur_mq_mese_med)}}</td>`;
        tr.addEventListener("click",()=>mostraDettaglio(c.codice_istat)); tb.appendChild(tr);
      }});
    }}
    tabella(sortedDesc.slice(0,30), "#qvi-top");
    tabella(sortedDesc.slice(-30).reverse(), "#qvi-bot");

    // Calcolatore
    function calcola() {{
      const cod=comCalc.value;
      const profKey=document.getElementById("qvi-calc-profilo").value;
      const modalita=document.getElementById("qvi-calc-modalita").value;
      const c=cod?COMUNI.find(x=>x.codice_istat===cod):null;
      const p=PROFILI[profKey];
      if(!c||!p) {{ document.getElementById("qvi-calc-out").textContent="Seleziona regione, provincia e comune per vedere il calcolo."; return; }}
      const paniere=paniereReg(profKey,c.regione,PANIERE,IPC);
      const casa=costoCasaAnnuo(p,modalita,c.affitto_eur_mq_mese_med,c.prezzo_acq_eur_mq_med);
      if(casa==null) {{
        document.getElementById("qvi-calc-out").innerHTML =
          '<div class="font-semibold">'+c.comune+' ('+c.sigla_provincia+')</div>' +
          '<div class="mt-2 italic text-slate-600">Dato OMI non ancora caricato per questo comune. ' +
          'Bulk OMI in corso: i piccoli comuni vengono ricaricati progressivamente. ' +
          'Prova "Già proprietario" per una stima senza affitto.</div>';
        return;
      }}
      const spesa=paniere+casa;
      const lordo=lordoPerNetto(spesa,p.figli,p.percettori);
      const nettoMediana=nettoDaLordo(c.reddito_mediana||0,p.figli);
      const residuo=nettoMediana-spesa;
      const colorClass=residuo>0?"bg-emerald-100 text-emerald-900":"bg-rose-100 text-rose-900";
      document.getElementById("qvi-calc-out").innerHTML =
        `<div class="font-semibold">${{c.comune}} <span class="text-slate-500">(${{c.sigla_provincia}})</span> &middot; ${{p.label}} &middot; ${{p.mq}} mq &middot; ${{modalita}}</div>` +
        `<div class="mt-3 grid sm:grid-cols-2 gap-2">` +
        `<div>Spesa annua casa: <b>${{fmt(casa)}}</b></div>` +
        `<div>Spesa altre voci (paniere): <b>${{fmt(paniere)}}</b></div>` +
        `<div>Spesa totale annua: <b>${{fmt(spesa)}}</b></div>` +
        `<div>Reddito lordo necessario: <b>${{fmt(lordo)}}</b></div>` +
        `<div>Mediana lorda comunale: <b>${{fmt(c.reddito_mediana)}}</b></div>` +
        `<div>Mediana netta stimata: <b>${{fmt(nettoMediana)}}</b></div>` +
        `</div>` +
        `<div class="mt-3 inline-block px-3 py-1.5 rounded-lg font-semibold ${{colorClass}}">Residuo annuo dalla mediana: ${{fmt(residuo)}}</div>`;
      simulaReddito();
    }}
    // Cambiare comune nel calcolatore aggiorna anche dettaglio + simulatore
    comCalc.addEventListener("change", () => {{ calcola(); if(comCalc.value) mostraDettaglio(comCalc.value); }});
    document.getElementById("qvi-calc-profilo").addEventListener("change", calcola);
    document.getElementById("qvi-calc-modalita").addEventListener("change", calcola);

    // Simulatore IRPEF/flat
    function simulaReddito() {{
      const cod=document.getElementById("qvi-calc-comune").value;
      const profKey=document.getElementById("qvi-calc-profilo").value;
      const modalita=document.getElementById("qvi-calc-modalita").value;
      const regime=document.getElementById("qvi-sim-regime").value;
      const reddito=parseFloat(document.getElementById("qvi-sim-reddito").value)||0;
      const c=COMUNI.find(x=>x.codice_istat===cod); const p=PROFILI[profKey];
      if(!c||!p) {{ document.getElementById("qvi-sim-out").textContent="Seleziona un comune."; return; }}
      const paniere=paniereReg(profKey,c.regione,PANIERE,IPC);
      const casa=costoCasaAnnuo(p,modalita,c.affitto_eur_mq_mese_med,c.prezzo_acq_eur_mq_med);
      const spesa=casa!=null?paniere+casa:null;
      const nettoOrd=nettoDaLordo(reddito,p.figli);
      const nettoF15=nettoFlatTax(reddito,0.15);
      const nettoF05=nettoFlatTax(reddito,0.05);
      const nettoSel=regime==="ordinario"?nettoOrd:regime==="flat15"?nettoF15:nettoF05;
      const labelRegime=regime==="ordinario"?"IRPEF ordinario":regime==="flat15"?"Flat tax 15%":"Flat tax 5% (startup)";
      let avviso=""; if(regime!=="ordinario"&&reddito>FLAT_LIMIT) {{
        avviso=`<div class="mt-2 rounded bg-rose-100 text-rose-900 px-3 py-2">⚠ Sopra soglia forfettario (${{fmt(FLAT_LIMIT)}}). In ordinario: <b>${{fmt(nettoOrd)}}</b>.</div>`;
      }}
      let residuoBlock="";
      if(spesa!=null&&nettoSel!=null) {{
        const residuo=nettoSel-spesa;
        const colorClass=residuo>0?"bg-emerald-100 text-emerald-900":"bg-rose-100 text-rose-900";
        residuoBlock = `<div class="mt-2">Spesa annua nel comune: <b>${{fmt(spesa)}}</b> (casa ${{fmt(casa)}} + paniere ${{fmt(paniere)}})</div>` +
          `<div class="mt-2 inline-block px-3 py-1.5 rounded-lg font-semibold ${{colorClass}}">Residuo netto annuo: ${{fmt(residuo)}}</div>`;
      }} else if(spesa==null) {{
        residuoBlock=`<div class="mt-2 italic text-slate-600">Dato OMI casa non disponibile per ${{c.comune}}.</div>`;
      }}
      let confrontoBlock="";
      if(reddito<=FLAT_LIMIT) {{
        const diff=(nettoF15||0)-nettoOrd; const segno=diff>=0?"+":"";
        const diffClass=diff>=0?"bg-emerald-100 text-emerald-900":"bg-rose-100 text-rose-900";
        confrontoBlock = `<div class="mt-3 border-t border-blue-200 pt-3"><b>Confronto regimi su ${{fmt(reddito)}} lordo:</b><br>` +
          `· IRPEF ordinario → <b>${{fmt(nettoOrd)}}</b> (aliquota effettiva ${{((1-nettoOrd/reddito)*100).toFixed(1)}}%)<br>` +
          `· Flat tax 15% → <b>${{fmt(nettoF15)}}</b> <span class="inline-block ml-1 px-2 py-0.5 rounded text-xs ${{diffClass}}">${{segno}}${{fmt(diff)}} vs ord.</span><br>` +
          `· Flat tax 5% → <b>${{fmt(nettoF05)}}</b> (solo startup primi 5 anni)</div>`;
      }} else {{
        confrontoBlock = `<div class="mt-3 border-t border-blue-200 pt-3"><b>Sopra ${{fmt(FLAT_LIMIT)}}: solo regime ordinario applicabile.</b><br>Netto ordinario: <b>${{fmt(nettoOrd)}}</b> (aliquota effettiva ${{((1-nettoOrd/reddito)*100).toFixed(1)}}%)</div>`;
      }}
      document.getElementById("qvi-sim-out").innerHTML =
        `<div class="font-semibold">${{fmt(reddito)}} imponibile in ${{c.comune}} (${{c.sigla_provincia}}) &middot; ${{labelRegime}}</div>` +
        `<div class="mt-2">Reddito netto stimato: <b>${{fmt(nettoSel)}}</b></div>` +
        residuoBlock + avviso + confrontoBlock;
    }}
    document.getElementById("qvi-sim-reddito").addEventListener("input", simulaReddito);
    document.getElementById("qvi-sim-regime").addEventListener("change", simulaReddito);

    // Lazy fetch OMI per i comuni non ancora coperti dal bulk
    const AJAX_URL = '<?php echo esc_js(admin_url("admin-ajax.php")); ?>';
    function omiBlockHtml(c) {{
      if (c.omi_disponibile && c.prezzo_acq_eur_mq_med != null) {{
        return `<div><div class="text-xs uppercase text-slate-500 tracking-wide">OMI residenziale (${{payload.meta.semestre_omi||"—"}})</div>` +
          `<div class="mt-1">Acquisto: <b>${{fmt(c.prezzo_acq_eur_mq_med)}}/mq</b></div>` +
          `<div class="mt-1">Affitto: <b>${{fmtN(c.affitto_eur_mq_mese_med,2)}} €/mq mese</b></div></div>`;
      }}
      // Placeholder con ID per riempire dopo fetch
      return `<div><div class="text-xs uppercase text-slate-500 tracking-wide">OMI residenziale</div>` +
        `<div id="qvi-omi-live" class="mt-1 italic text-slate-500">Caricamento dati OMI live…</div></div>`;
    }}
    function fetchOmiAggregato(c) {{
      const params = new URLSearchParams({{
        action: 'qvi_omi_aggregato',
        pr: c.sigla_provincia, comune: c.comune
      }});
      return fetch(AJAX_URL + '?' + params.toString())
        .then(r => r.json())
        .then(j => j && j.success ? j.data : null)
        .catch(() => null);
    }}

    // Dettaglio
    window.mostraDettaglio = function(cod) {{
      const c=COMUNI.find(x=>x.codice_istat===cod); if(!c) return;
      document.getElementById("qvi-calc-comune").value=cod; calcola();

      const detailHtml =
        `<h3 class="text-xl font-semibold text-slate-900">${{c.comune}} <span class="text-slate-500 text-base">(${{c.sigla_provincia}}, ${{c.regione||""}})</span></h3>` +
        `<div class="mt-4 grid md:grid-cols-2 gap-4">` +
        `<div><div class="text-xs uppercase text-slate-500 tracking-wide">Distribuzione redditi</div>` +
        `<div class="mt-1">P10: <b>${{fmt(c.reddito_p10)}}</b> &middot; Mediana: <b>${{fmt(c.reddito_mediana)}}</b> &middot; P90: <b>${{fmt(c.reddito_p90)}}</b></div>` +
        `<div class="mt-1 text-sm text-slate-600">Disuguaglianza P90/P10 = ${{fmtN(c.ratio_p90_p10,2)}} &middot; ` +
        `% sotto 15k: ${{fmtN(c.pct_sotto_15k,1)}}% &middot; % sopra 55k: ${{fmtN(c.pct_sopra_55k,1)}}%</div></div>` +
        omiBlockHtml(c) +
        `<div class="md:col-span-2"><div class="text-xs uppercase text-slate-500 tracking-wide">Reddito sostenibile (affitto)</div>` +
        `<div class="mt-1">Single: <b>${{fmt(c.rs_single_affitto)}}</b> &middot; Coppia 1 stip.: <b>${{fmt(c.rs_coppia_affitto)}}</b> &middot; Coppia +2 figli: <b>${{fmt(c.rs_coppia_2f_affitto)}}</b></div></div>` +
        `<div class="md:col-span-2"><div class="text-xs uppercase text-slate-500 tracking-wide">Indice qualità</div>` +
        `<div class="mt-1 text-2xl font-bold text-slate-900">${{fmtN(c.indice_qualita,1)}}/100</div></div>` +
        `</div>`;
      document.getElementById("qvi-dettaglio-body").innerHTML = detailHtml;
      document.getElementById("qvi-dettaglio").scrollIntoView({{behavior:"smooth", block:"nearest"}});

      // Lazy fetch OMI: solo se non disponibile nel payload
      if (!c.omi_disponibile || c.prezzo_acq_eur_mq_med == null) {{
        fetchOmiAggregato(c).then(d => {{
          const el = document.getElementById("qvi-omi-live"); if (!el) return;
          if (!d) {{ el.innerHTML = '<i class="text-rose-700">OMI non disponibile per questo comune.</i>'; return; }}
          // Aggiorna anche l'oggetto in memoria + ricalcolo calcolatore
          c.prezzo_acq_eur_mq_med = d.prezzo_acq_eur_mq_med;
          c.affitto_eur_mq_mese_med = d.affitto_eur_mq_mese_med;
          c.omi_disponibile = !!(d.prezzo_acq_eur_mq_med || d.affitto_eur_mq_mese_med);
          el.outerHTML = `<div class="mt-1">Acquisto: <b>${{fmt(d.prezzo_acq_eur_mq_med)}}/mq</b></div>` +
            `<div class="mt-1">Affitto: <b>${{fmtN(d.affitto_eur_mq_mese_med,2)}} €/mq mese</b></div>` +
            `<div class="mt-1 text-xs text-slate-500">Caricato live (${{d.n_zone_con_dati||"?"}}/${{d.n_zone||"?"}} zone)</div>`;
          calcola(); simulaReddito();
        }});
      }}
    }};

    // Init defaults
    if(COMUNI.length) {{ document.getElementById("qvi-calc-comune").value=COMUNI[0].codice_istat; calcola(); simulaReddito(); }}
  }}

  // Fetch payload async
  fetch(PAYLOAD_URL).then(r=>r.json()).then(init).catch(err => {{
    document.getElementById("qvi-map").innerHTML = '<div class="p-4 text-rose-700 text-sm">Errore caricamento dati: '+err+'</div>';
  }});
}})();
</script>

<?php wp_footer(); ?>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(build_dashboard())
