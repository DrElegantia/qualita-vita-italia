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

    # Payload separato per fetch async dal template WP (riduce LCP)
    dash_payload = {
        "meta": payload["meta"],
        "comuni": payload["comuni"],
        "centroidi": cent_subset,
    }
    dash_json = OUT_DATA / "comuni-dashboard.json"
    dash_json.write_text(json.dumps(dash_payload, ensure_ascii=False, separators=(",", ":")))
    log.info("scritto %s (%.1f KB) per fetch async",
             dash_json, dash_json.stat().st_size / 1024)

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
$payload_url = content_url('/uploads/qualita-vita/comuni-dashboard.json');
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
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
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
          <p><?php lc_e('Ogni cerchio è un comune. Dimensione: numero di contribuenti. Colore: indicatore selezionato. Click su un punto per il dettaglio. Filtra regione e indicatore per riorientare la lettura.'); ?></p>
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
            </select>
          </label>
          <label class="text-sm"><?php lc_e('Regione'); ?>
            <select id="qvi-regione" class="ml-2 rounded border border-slate-300 px-2 py-1 text-sm">
              <option value=""><?php lc_e('Tutte'); ?></option>
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
          <label class="text-sm"><?php lc_e('Comune'); ?>
            <select id="qvi-calc-comune" class="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"></select>
          </label>
          <label class="text-sm"><?php lc_e('Profilo'); ?>
            <select id="qvi-calc-profilo" class="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm">
              <option value="single">1 adulto</option>
              <option value="coppia" selected>Coppia, 1 stipendio</option>
              <option value="coppia2">Coppia, 2 stipendi</option>
              <option value="coppia_1f">Coppia + 1 figlio, 1 stip.</option>
              <option value="coppia_2f">Coppia + 2 figli, 2 stip.</option>
            </select>
          </label>
          <label class="text-sm"><?php lc_e('Casa'); ?>
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

      <!-- FONTI -->
      <section class="mt-8 glass shadow-float rounded-3xl p-8 md:p-10">
        <h2 class="text-2xl md:text-3xl font-semibold tracking-tight text-slate-950"><?php lc_e('Fonti e note metodologiche'); ?></h2>
        <ul class="mt-4 space-y-2 text-sm text-slate-700">
          <li><strong>MEF Dipartimento delle Finanze</strong>: dichiarazioni IRPEF su base comunale, anno {anno}. P10/Q1/mediana/Q3/P90 stimati per interpolazione lineare nelle 8 fasce di reddito complessivo. La fascia >120k è aperta: P95/P99 non ricavabili senza assunzione esterna. Comuni con &lt;500 contribuenti: stime rumorose, segnalate.</li>
          <li><strong>Agenzia delle Entrate OMI</strong>: quotazioni immobiliari semestre {sem}. Mediana sui valori centrali min-max delle zone OMI di ciascun comune, tipologie "Abitazioni civili" + "Abitazioni di tipo economico", stato "Normale".</li>
          <li><strong>Indice qualità</strong>: 60% residuo netto annuo (mediana netta &minus; spesa minima single in affitto) + 40% accessibilità casa (1 - prezzo acq normalizzato). Estendibile con criminalità e servizi BES (in attesa endpoint ISTAT).</li>
          <li><strong>IRPEF 2025</strong>: scaglioni 23% / 35% / 43%, detrazioni dipendente, addizionali regionale 1,73% e comunale 0,5% medie. Flat tax forfettario: imposta sostitutiva 15% (5% startup primi 5 anni), no addizionali, no detrazioni, soglia 85k.</li>
          <li><strong>Codice e dati</strong>: <a href="https://github.com/DrElegantia/qualita-vita-italia" target="_blank" rel="noopener">github.com/DrElegantia/qualita-vita-italia</a> (MIT). Pipeline auto-aggiornante mensile.</li>
        </ul>
      </section>

      <?php if (file_exists(__DIR__ . '/partials/footer-condividi.php')) include __DIR__ . '/partials/footer-condividi.php'; ?>

    </main>
  </div>
</div>

<script>
(function() {{
  const PAYLOAD_URL = <?php echo wp_json_encode($payload_url); ?>;
  const PALETTE = {{ orange: "#F17820", blue: "#00355F", green: "#1b7f3a", red: "#c0392b", neutral: "#475569" }};

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
  function fmt(n) {{ if(n==null||isNaN(n))return "—"; return Math.round(n).toLocaleString("it-IT")+" €"; }}
  function fmtN(n,d=2) {{ if(n==null||isNaN(n))return "—"; return n.toFixed(d); }}

  let DATA = null;

  function init(payload) {{
    DATA = payload;
    const COMUNI = payload.comuni || [];
    const CENTROIDI = payload.centroidi || {{}};
    const PROFILI = payload.meta.profili;
    const PANIERE = payload.meta.paniere_non_casa;

    // KPI
    const kpiHtml = [
      ['<?php lc_e("Comuni mappati"); ?>', COMUNI.length],
      ['<?php lc_e("Mediana naz. (€)"); ?>', fmt(payload.meta.mediana_naz || null)],
      ['<?php lc_e("Anno redditi"); ?>', payload.meta.anno_redditi || '—'],
      ['<?php lc_e("Semestre OMI"); ?>', payload.meta.semestre_omi || '—'],
    ].map(([k,v]) => `<div class="rounded-2xl bg-white/70 backdrop-blur border border-white/60 p-4 shadow-sm"><div class="text-xs uppercase tracking-wide text-slate-500">${{k}}</div><div class="mt-1 text-xl font-semibold text-slate-900">${{v}}</div></div>`).join("");
    document.getElementById("qvi-kpi").innerHTML = kpiHtml;

    // Regioni dropdown
    const regioni = [...new Set(COMUNI.map(c=>c.regione).filter(Boolean))].sort();
    const regSel = document.getElementById("qvi-regione");
    for(const r of regioni) {{ const o=document.createElement("option"); o.value=r; o.text=r; regSel.appendChild(o); }}

    // Comuni dropdown calc
    const comuneSel = document.getElementById("qvi-calc-comune");
    [...COMUNI].sort((a,b)=>a.comune.localeCompare(b.comune)).forEach(c => {{
      const o=document.createElement("option"); o.value=c.codice_istat; o.text=c.comune+" ("+c.sigla_provincia+")"; comuneSel.appendChild(o);
    }});

    function buildMap(filtro) {{
      const subset = COMUNI.filter(c => CENTROIDI[c.codice_istat] && (!filtro || c.regione===filtro));
      const indic = document.getElementById("qvi-indicatore").value;
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
      const trace = {{
        type:"scattermapbox", mode:"markers", lat:lats, lon:lons,
        marker: {{ size:sizes, color:z, colorscale: indic==="indice_qualita"||indic.startsWith("residuo")||indic==="reddito_mediana" ? "RdYlGn" : "RdYlGn_r", showscale:true, colorbar:{{title:indic, thickness:14, len:0.7}} }},
        text:text, hovertemplate:"%{{text}}<extra></extra>",
        customdata: subset.map(c=>c.codice_istat),
      }};
      const layout = {{ mapbox:{{style:"open-street-map", center:{{lat:42.5, lon:12.5}}, zoom:5.2}}, margin:{{t:10,b:10,l:10,r:10}}, height:560, paper_bgcolor:"rgba(0,0,0,0)" }};
      Plotly.newPlot("qvi-map", [trace], layout, {{displayModeBar:false, responsive:true}}).then(gd => {{
        gd.on("plotly_click", e => mostraDettaglio(e.points[0].customdata));
      }});
    }}
    buildMap("");
    document.getElementById("qvi-indicatore").addEventListener("change", () => buildMap(regSel.value));
    document.getElementById("qvi-regione").addEventListener("change", () => buildMap(regSel.value));

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
      const cod=document.getElementById("qvi-calc-comune").value;
      const profKey=document.getElementById("qvi-calc-profilo").value;
      const modalita=document.getElementById("qvi-calc-modalita").value;
      const c=COMUNI.find(x=>x.codice_istat===cod); const p=PROFILI[profKey];
      if(!c||!p) {{ document.getElementById("qvi-calc-out").textContent="Seleziona un comune."; return; }}
      const paniere=PANIERE[profKey];
      const casa=costoCasaAnnuo(p,modalita,c.affitto_eur_mq_mese_med,c.prezzo_acq_eur_mq_med);
      if(casa==null) {{ document.getElementById("qvi-calc-out").innerHTML="<i>Dati casa non disponibili per "+c.comune+".</i>"; return; }}
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
    document.getElementById("qvi-calc-comune").addEventListener("change", calcola);
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
      const paniere=PANIERE[profKey];
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

    // Dettaglio
    window.mostraDettaglio = function(cod) {{
      const c=COMUNI.find(x=>x.codice_istat===cod); if(!c) return;
      document.getElementById("qvi-calc-comune").value=cod; calcola();
      document.getElementById("qvi-dettaglio-body").innerHTML =
        `<h3 class="text-xl font-semibold text-slate-900">${{c.comune}} <span class="text-slate-500 text-base">(${{c.sigla_provincia}}, ${{c.regione||""}})</span></h3>` +
        `<div class="mt-4 grid md:grid-cols-2 gap-4">` +
        `<div><div class="text-xs uppercase text-slate-500 tracking-wide">Distribuzione redditi</div>` +
        `<div class="mt-1">P10: <b>${{fmt(c.reddito_p10)}}</b> &middot; Mediana: <b>${{fmt(c.reddito_mediana)}}</b> &middot; P90: <b>${{fmt(c.reddito_p90)}}</b></div>` +
        `<div class="mt-1 text-sm text-slate-600">Disuguaglianza P90/P10 = ${{fmtN(c.ratio_p90_p10,2)}} &middot; ` +
        `% sotto 15k: ${{fmtN(c.pct_sotto_15k,1)}}% &middot; % sopra 55k: ${{fmtN(c.pct_sopra_55k,1)}}%</div></div>` +
        `<div><div class="text-xs uppercase text-slate-500 tracking-wide">OMI residenziale (${{payload.meta.semestre_omi||"—"}})</div>` +
        `<div class="mt-1">Acquisto: <b>${{fmt(c.prezzo_acq_eur_mq_med)}}/mq</b></div>` +
        `<div class="mt-1">Affitto: <b>${{fmtN(c.affitto_eur_mq_mese_med,2)}} €/mq mese</b></div></div>` +
        `<div class="md:col-span-2"><div class="text-xs uppercase text-slate-500 tracking-wide">Reddito sostenibile (affitto)</div>` +
        `<div class="mt-1">Single: <b>${{fmt(c.rs_single_affitto)}}</b> &middot; Coppia 1 stip.: <b>${{fmt(c.rs_coppia_affitto)}}</b> &middot; Coppia +2 figli: <b>${{fmt(c.rs_coppia_2f_affitto)}}</b></div></div>` +
        `<div class="md:col-span-2"><div class="text-xs uppercase text-slate-500 tracking-wide">Indice qualità</div>` +
        `<div class="mt-1 text-2xl font-bold text-slate-900">${{fmtN(c.indice_qualita,1)}}/100</div></div>` +
        `</div>`;
      document.getElementById("qvi-dettaglio").scrollIntoView({{behavior:"smooth", block:"nearest"}});
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
