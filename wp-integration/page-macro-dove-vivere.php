<?php
/*
Template Name: Macro Dove vivere
Template Post Type: page
*/
if (!defined('ABSPATH')) exit;

global $lc_custom_og;
$lc_custom_og = true;
$_lang = lc_get_lang();

$site_name  = get_bloginfo('name');
$page_title = lc__('Dove si vive bene in Italia: indice qualità della vita per comune');
$full_title = $page_title . ' | ' . $site_name;

$desc = lc__('Reddito mediano (MEF), prezzi case e affitti OMI, costo della vita stimato per 619+ comuni italiani. Mappa interattiva, classifica top/bottom 30, calcolatore reddito sostenibile e simulatore IRPEF ordinario vs flat tax. Dati MEF 2024 + OMI semestre 20252.0.');

$permalink = home_url('/macro/dove-vivere/');
$canonical = $permalink;

$og_image = function_exists('lc_og_image_url')
  ? lc_og_image_url($page_title, 'dove-vivere')
  : '';

$share_url   = $permalink;
$share_title = $page_title;
$fb  = 'https://www.facebook.com/sharer/sharer.php?u=' . rawurlencode($share_url);
$x   = 'https://twitter.com/intent/tweet?url=' . rawurlencode($share_url) . '&text=' . rawurlencode($share_title);
$ln  = 'https://www.linkedin.com/sharing/share-offsite/?url=' . rawurlencode($share_url);
$pin = 'https://pinterest.com/pin/create/button/?url=' . rawurlencode($share_url) . '&description=' . rawurlencode($share_title);
if ($og_image) { $pin .= '&media=' . rawurlencode($og_image); }

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
    'slug'        => 'dove-vivere',
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
          <li><strong>MEF Dipartimento delle Finanze</strong>: dichiarazioni IRPEF su base comunale, anno 2024. P10/Q1/mediana/Q3/P90 stimati per interpolazione lineare nelle 8 fasce di reddito complessivo. La fascia >120k è aperta: P95/P99 non ricavabili senza assunzione esterna. Comuni con &lt;500 contribuenti: stime rumorose, segnalate.</li>
          <li><strong>Agenzia delle Entrate OMI</strong>: quotazioni immobiliari semestre 20252.0. Mediana sui valori centrali min-max delle zone OMI di ciascun comune, tipologie "Abitazioni civili" + "Abitazioni di tipo economico", stato "Normale".</li>
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
(function() {
  const PAYLOAD_URL = <?php echo wp_json_encode($payload_url); ?>;
  const PALETTE = { orange: "#F17820", blue: "#00355F", green: "#1b7f3a", red: "#c0392b", neutral: "#475569" };

  // IRPEF 2025 + flat tax (clientside)
  const SCAGLIONI = [[28000, 0.23], [50000, 0.35], [Infinity, 0.43]];
  const ADD_REG = 0.0173, ADD_COM = 0.005, DETR_BASE = 1955, DETR_FIG = 950;
  const FLAT_LIMIT = 85000;
  function irpefLorda(r) { let imp=0,prev=0; for(const[s,a] of SCAGLIONI){ if(r<=prev)break; const cap=Math.min(r,s); imp+=(cap-prev)*a; prev=cap; } return imp; }
  function detrazioneDip(r) { if(r<=15000)return DETR_BASE; if(r<=28000)return 1910+1190*(28000-r)/13000; if(r<=50000)return 1910*(50000-r)/22000; return 0; }
  function nettoDaLordo(lordo,nFigli) { const ip=Math.max(0,irpefLorda(lordo)-detrazioneDip(lordo)-nFigli*DETR_FIG); const add=lordo*(ADD_REG+ADD_COM); return lordo-ip-add; }
  function nettoFlatTax(lordo,al) { if(lordo>FLAT_LIMIT)return null; return lordo*(1-al); }
  function lordoPerNetto(target,nFigli,nP) { if(target<=0)return 0; let lo=0,hi=250000; const tp=target/nP; for(let i=0;i<40;i++){ const m=(lo+hi)/2; if(nettoDaLordo(m,nFigli)<tp)lo=m; else hi=m; } return((lo+hi)/2)*nP; }
  function costoCasaAnnuo(p,m,af,ac) { if(m==="affitto")return af?p.mq*af*12:null; if(m==="proprieta")return 1500; return null; }
  function fmt(n) { if(n==null||isNaN(n))return "—"; return Math.round(n).toLocaleString("it-IT")+" €"; }
  function fmtN(n,d=2) { if(n==null||isNaN(n))return "—"; return n.toFixed(d); }

  let DATA = null;

  function init(payload) {
    DATA = payload;
    const COMUNI = payload.comuni || [];
    const CENTROIDI = payload.centroidi || {};
    const PROFILI = payload.meta.profili;
    const PANIERE = payload.meta.paniere_non_casa;

    // KPI
    const kpiHtml = [
      ['<?php lc_e("Comuni mappati"); ?>', COMUNI.length],
      ['<?php lc_e("Mediana naz. (€)"); ?>', fmt(payload.meta.mediana_naz || null)],
      ['<?php lc_e("Anno redditi"); ?>', payload.meta.anno_redditi || '—'],
      ['<?php lc_e("Semestre OMI"); ?>', payload.meta.semestre_omi || '—'],
    ].map(([k,v]) => `<div class="rounded-2xl bg-white/70 backdrop-blur border border-white/60 p-4 shadow-sm"><div class="text-xs uppercase tracking-wide text-slate-500">${k}</div><div class="mt-1 text-xl font-semibold text-slate-900">${v}</div></div>`).join("");
    document.getElementById("qvi-kpi").innerHTML = kpiHtml;

    // Regioni dropdown
    const regioni = [...new Set(COMUNI.map(c=>c.regione).filter(Boolean))].sort();
    const regSel = document.getElementById("qvi-regione");
    for(const r of regioni) { const o=document.createElement("option"); o.value=r; o.text=r; regSel.appendChild(o); }

    // Comuni dropdown calc
    const comuneSel = document.getElementById("qvi-calc-comune");
    [...COMUNI].sort((a,b)=>a.comune.localeCompare(b.comune)).forEach(c => {
      const o=document.createElement("option"); o.value=c.codice_istat; o.text=c.comune+" ("+c.sigla_provincia+")"; comuneSel.appendChild(o);
    });

    function buildMap(filtro) {
      const subset = COMUNI.filter(c => CENTROIDI[c.codice_istat] && (!filtro || c.regione===filtro));
      const indic = document.getElementById("qvi-indicatore").value;
      const z = subset.map(c => c[indic]);
      const text = subset.map(c =>
        `<b>${c.comune}</b> (${c.sigla_provincia})<br>` +
        `Mediana: ${fmt(c.reddito_mediana)}<br>` +
        `Affitto: ${fmtN(c.affitto_eur_mq_mese_med)} €/mq mese<br>` +
        `Prezzo acq: ${fmt(c.prezzo_acq_eur_mq_med)}/mq<br>` +
        `Indice: ${fmtN(c.indice_qualita,1)}/100`
      );
      const lats=subset.map(c=>CENTROIDI[c.codice_istat][0]);
      const lons=subset.map(c=>CENTROIDI[c.codice_istat][1]);
      const sizes=subset.map(c=>Math.max(8, Math.min(40, Math.log10(c.n_contribuenti||1000)*6)));
      const trace = {
        type:"scattermapbox", mode:"markers", lat:lats, lon:lons,
        marker: { size:sizes, color:z, colorscale: indic==="indice_qualita"||indic.startsWith("residuo")||indic==="reddito_mediana" ? "RdYlGn" : "RdYlGn_r", showscale:true, colorbar:{title:indic, thickness:14, len:0.7} },
        text:text, hovertemplate:"%{text}<extra></extra>",
        customdata: subset.map(c=>c.codice_istat),
      };
      const layout = { mapbox:{style:"open-street-map", center:{lat:42.5, lon:12.5}, zoom:5.2}, margin:{t:10,b:10,l:10,r:10}, height:560, paper_bgcolor:"rgba(0,0,0,0)" };
      Plotly.newPlot("qvi-map", [trace], layout, {displayModeBar:false, responsive:true}).then(gd => {
        gd.on("plotly_click", e => mostraDettaglio(e.points[0].customdata));
      });
    }
    buildMap("");
    document.getElementById("qvi-indicatore").addEventListener("change", () => buildMap(regSel.value));
    document.getElementById("qvi-regione").addEventListener("change", () => buildMap(regSel.value));

    // Tabelle
    const sortedDesc = [...COMUNI].filter(c=>c.indice_qualita!=null).sort((a,b)=>b.indice_qualita-a.indice_qualita);
    function tabella(arr, sel) {
      const tb = document.querySelector(sel+" tbody"); tb.innerHTML="";
      arr.forEach((c,i) => {
        const tr=document.createElement("tr"); tr.className="border-t border-slate-200 cursor-pointer hover:bg-slate-50";
        tr.innerHTML = `<td class="px-2 py-1.5">${i+1}</td><td class="px-2 py-1.5">${c.comune}</td><td class="px-2 py-1.5">${c.sigla_provincia}</td>` +
          `<td class="px-2 py-1.5 text-right font-medium">${fmtN(c.indice_qualita,1)}</td>` +
          `<td class="px-2 py-1.5 text-right">${fmt(c.reddito_mediana)}</td>` +
          `<td class="px-2 py-1.5 text-right">${fmtN(c.affitto_eur_mq_mese_med)}</td>`;
        tr.addEventListener("click",()=>mostraDettaglio(c.codice_istat)); tb.appendChild(tr);
      });
    }
    tabella(sortedDesc.slice(0,30), "#qvi-top");
    tabella(sortedDesc.slice(-30).reverse(), "#qvi-bot");

    // Calcolatore
    function calcola() {
      const cod=document.getElementById("qvi-calc-comune").value;
      const profKey=document.getElementById("qvi-calc-profilo").value;
      const modalita=document.getElementById("qvi-calc-modalita").value;
      const c=COMUNI.find(x=>x.codice_istat===cod); const p=PROFILI[profKey];
      if(!c||!p) { document.getElementById("qvi-calc-out").textContent="Seleziona un comune."; return; }
      const paniere=PANIERE[profKey];
      const casa=costoCasaAnnuo(p,modalita,c.affitto_eur_mq_mese_med,c.prezzo_acq_eur_mq_med);
      if(casa==null) { document.getElementById("qvi-calc-out").innerHTML="<i>Dati casa non disponibili per "+c.comune+".</i>"; return; }
      const spesa=paniere+casa;
      const lordo=lordoPerNetto(spesa,p.figli,p.percettori);
      const nettoMediana=nettoDaLordo(c.reddito_mediana||0,p.figli);
      const residuo=nettoMediana-spesa;
      const colorClass=residuo>0?"bg-emerald-100 text-emerald-900":"bg-rose-100 text-rose-900";
      document.getElementById("qvi-calc-out").innerHTML =
        `<div class="font-semibold">${c.comune} <span class="text-slate-500">(${c.sigla_provincia})</span> &middot; ${p.label} &middot; ${p.mq} mq &middot; ${modalita}</div>` +
        `<div class="mt-3 grid sm:grid-cols-2 gap-2">` +
        `<div>Spesa annua casa: <b>${fmt(casa)}</b></div>` +
        `<div>Spesa altre voci (paniere): <b>${fmt(paniere)}</b></div>` +
        `<div>Spesa totale annua: <b>${fmt(spesa)}</b></div>` +
        `<div>Reddito lordo necessario: <b>${fmt(lordo)}</b></div>` +
        `<div>Mediana lorda comunale: <b>${fmt(c.reddito_mediana)}</b></div>` +
        `<div>Mediana netta stimata: <b>${fmt(nettoMediana)}</b></div>` +
        `</div>` +
        `<div class="mt-3 inline-block px-3 py-1.5 rounded-lg font-semibold ${colorClass}">Residuo annuo dalla mediana: ${fmt(residuo)}</div>`;
      simulaReddito();
    }
    document.getElementById("qvi-calc-comune").addEventListener("change", calcola);
    document.getElementById("qvi-calc-profilo").addEventListener("change", calcola);
    document.getElementById("qvi-calc-modalita").addEventListener("change", calcola);

    // Simulatore IRPEF/flat
    function simulaReddito() {
      const cod=document.getElementById("qvi-calc-comune").value;
      const profKey=document.getElementById("qvi-calc-profilo").value;
      const modalita=document.getElementById("qvi-calc-modalita").value;
      const regime=document.getElementById("qvi-sim-regime").value;
      const reddito=parseFloat(document.getElementById("qvi-sim-reddito").value)||0;
      const c=COMUNI.find(x=>x.codice_istat===cod); const p=PROFILI[profKey];
      if(!c||!p) { document.getElementById("qvi-sim-out").textContent="Seleziona un comune."; return; }
      const paniere=PANIERE[profKey];
      const casa=costoCasaAnnuo(p,modalita,c.affitto_eur_mq_mese_med,c.prezzo_acq_eur_mq_med);
      const spesa=casa!=null?paniere+casa:null;
      const nettoOrd=nettoDaLordo(reddito,p.figli);
      const nettoF15=nettoFlatTax(reddito,0.15);
      const nettoF05=nettoFlatTax(reddito,0.05);
      const nettoSel=regime==="ordinario"?nettoOrd:regime==="flat15"?nettoF15:nettoF05;
      const labelRegime=regime==="ordinario"?"IRPEF ordinario":regime==="flat15"?"Flat tax 15%":"Flat tax 5% (startup)";
      let avviso=""; if(regime!=="ordinario"&&reddito>FLAT_LIMIT) {
        avviso=`<div class="mt-2 rounded bg-rose-100 text-rose-900 px-3 py-2">⚠ Sopra soglia forfettario (${fmt(FLAT_LIMIT)}). In ordinario: <b>${fmt(nettoOrd)}</b>.</div>`;
      }
      let residuoBlock="";
      if(spesa!=null&&nettoSel!=null) {
        const residuo=nettoSel-spesa;
        const colorClass=residuo>0?"bg-emerald-100 text-emerald-900":"bg-rose-100 text-rose-900";
        residuoBlock = `<div class="mt-2">Spesa annua nel comune: <b>${fmt(spesa)}</b> (casa ${fmt(casa)} + paniere ${fmt(paniere)})</div>` +
          `<div class="mt-2 inline-block px-3 py-1.5 rounded-lg font-semibold ${colorClass}">Residuo netto annuo: ${fmt(residuo)}</div>`;
      } else if(spesa==null) {
        residuoBlock=`<div class="mt-2 italic text-slate-600">Dato OMI casa non disponibile per ${c.comune}.</div>`;
      }
      let confrontoBlock="";
      if(reddito<=FLAT_LIMIT) {
        const diff=(nettoF15||0)-nettoOrd; const segno=diff>=0?"+":"";
        const diffClass=diff>=0?"bg-emerald-100 text-emerald-900":"bg-rose-100 text-rose-900";
        confrontoBlock = `<div class="mt-3 border-t border-blue-200 pt-3"><b>Confronto regimi su ${fmt(reddito)} lordo:</b><br>` +
          `· IRPEF ordinario → <b>${fmt(nettoOrd)}</b> (aliquota effettiva ${((1-nettoOrd/reddito)*100).toFixed(1)}%)<br>` +
          `· Flat tax 15% → <b>${fmt(nettoF15)}</b> <span class="inline-block ml-1 px-2 py-0.5 rounded text-xs ${diffClass}">${segno}${fmt(diff)} vs ord.</span><br>` +
          `· Flat tax 5% → <b>${fmt(nettoF05)}</b> (solo startup primi 5 anni)</div>`;
      } else {
        confrontoBlock = `<div class="mt-3 border-t border-blue-200 pt-3"><b>Sopra ${fmt(FLAT_LIMIT)}: solo regime ordinario applicabile.</b><br>Netto ordinario: <b>${fmt(nettoOrd)}</b> (aliquota effettiva ${((1-nettoOrd/reddito)*100).toFixed(1)}%)</div>`;
      }
      document.getElementById("qvi-sim-out").innerHTML =
        `<div class="font-semibold">${fmt(reddito)} imponibile in ${c.comune} (${c.sigla_provincia}) &middot; ${labelRegime}</div>` +
        `<div class="mt-2">Reddito netto stimato: <b>${fmt(nettoSel)}</b></div>` +
        residuoBlock + avviso + confrontoBlock;
    }
    document.getElementById("qvi-sim-reddito").addEventListener("input", simulaReddito);
    document.getElementById("qvi-sim-regime").addEventListener("change", simulaReddito);

    // Dettaglio
    window.mostraDettaglio = function(cod) {
      const c=COMUNI.find(x=>x.codice_istat===cod); if(!c) return;
      document.getElementById("qvi-calc-comune").value=cod; calcola();
      document.getElementById("qvi-dettaglio-body").innerHTML =
        `<h3 class="text-xl font-semibold text-slate-900">${c.comune} <span class="text-slate-500 text-base">(${c.sigla_provincia}, ${c.regione||""})</span></h3>` +
        `<div class="mt-4 grid md:grid-cols-2 gap-4">` +
        `<div><div class="text-xs uppercase text-slate-500 tracking-wide">Distribuzione redditi</div>` +
        `<div class="mt-1">P10: <b>${fmt(c.reddito_p10)}</b> &middot; Mediana: <b>${fmt(c.reddito_mediana)}</b> &middot; P90: <b>${fmt(c.reddito_p90)}</b></div>` +
        `<div class="mt-1 text-sm text-slate-600">Disuguaglianza P90/P10 = ${fmtN(c.ratio_p90_p10,2)} &middot; ` +
        `% sotto 15k: ${fmtN(c.pct_sotto_15k,1)}% &middot; % sopra 55k: ${fmtN(c.pct_sopra_55k,1)}%</div></div>` +
        `<div><div class="text-xs uppercase text-slate-500 tracking-wide">OMI residenziale (${payload.meta.semestre_omi||"—"})</div>` +
        `<div class="mt-1">Acquisto: <b>${fmt(c.prezzo_acq_eur_mq_med)}/mq</b></div>` +
        `<div class="mt-1">Affitto: <b>${fmtN(c.affitto_eur_mq_mese_med,2)} €/mq mese</b></div></div>` +
        `<div class="md:col-span-2"><div class="text-xs uppercase text-slate-500 tracking-wide">Reddito sostenibile (affitto)</div>` +
        `<div class="mt-1">Single: <b>${fmt(c.rs_single_affitto)}</b> &middot; Coppia 1 stip.: <b>${fmt(c.rs_coppia_affitto)}</b> &middot; Coppia +2 figli: <b>${fmt(c.rs_coppia_2f_affitto)}</b></div></div>` +
        `<div class="md:col-span-2"><div class="text-xs uppercase text-slate-500 tracking-wide">Indice qualità</div>` +
        `<div class="mt-1 text-2xl font-bold text-slate-900">${fmtN(c.indice_qualita,1)}/100</div></div>` +
        `</div>`;
      document.getElementById("qvi-dettaglio").scrollIntoView({behavior:"smooth", block:"nearest"});
    };

    // Init defaults
    if(COMUNI.length) { document.getElementById("qvi-calc-comune").value=COMUNI[0].codice_istat; calcola(); simulaReddito(); }
  }

  // Fetch payload async
  fetch(PAYLOAD_URL).then(r=>r.json()).then(init).catch(err => {
    document.getElementById("qvi-map").innerHTML = '<div class="p-4 text-rose-700 text-sm">Errore caricamento dati: '+err+'</div>';
  });
})();
</script>

<?php wp_footer(); ?>
</body>
</html>
