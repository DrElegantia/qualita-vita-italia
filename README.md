# Qualità della vita Italia — dashboard per comune

Dashboard interattiva che combina **reddito mediano** (MEF dichiarazioni IRPEF), **costo della vita** (ISTAT IPC regionale + OMI Agenzia Entrate per la casa), **criminalità** (ISTAT delittuosità), **servizi** (ISTAT BES) per stimare dove si vive bene in Italia, fino al livello del singolo comune e, on-demand, della zona OMI dentro i capoluoghi.

Servita su [umbertobertonelli.it/macro/dove-vivere/](https://www.umbertobertonelli.it/macro/dove-vivere/).

## Cosa contiene la dashboard

1. **Vista nazionale (mappa choropleth)** — 7.900 comuni, indicatore selezionabile: reddito mediano, indice costo vita, reddito netto residuo, qualità vita composita.
2. **Pannello dettaglio comune** (al click) — distribuzione redditi (P10/Q1/mediana/Q3/P90 da fasce MEF), affitto e prezzi al mq per zona OMI, indice servizi e sicurezza provinciale, ranking nazionale.
3. **Calcolatore reddito sostenibile** — input: composizione familiare (adulti, figli, percettori) + modalità abitativa. Output: reddito lordo necessario per coprire spese nel comune scelto, e quanto residua dal reddito mediano locale.
4. **Pagina classifica top 100 / bottom 100** stand-alone, con filtri per regione, dimensione comune, profilo familiare e pesi configurabili dell'indice.

## Architettura dati

| Indicatore | Fonte | Granularità | Update |
|---|---|---|---|
| Reddito comunale (medio + 8 fasce) | MEF dichiarazioni IRPEF | comune | annuale |
| Decili+quartili reddito (P10, Q1, mediana, Q3, P90) | interpolazione lineare nelle fasce MEF | comune | annuale |
| OMI valore aggregato (vista nazionale) | Agenzia Entrate Open Data semestrale (bulk ZIP) | comune | semestrale |
| OMI zone dettagliate (vista comune) | API GEOPOI on-demand (lazy 90gg cache) | zona OMI | semestrale |
| Costo vita proxy | ISTAT IPC regionale + OMI casa | comune | trimestrale |
| Criminalità | ISTAT delittuosità provinciale | provincia | annuale |
| Servizi | ISTAT BES provinciale | provincia | annuale |
| Geometrie | ISTAT confini amministrativi | poligoni | raro |

### Indice composito qualità della vita (default)

```
score = 0.40 * reddito_netto_residuo_norm
      + 0.20 * (1 - costo_vita_norm)
      + 0.15 * servizi_BES_norm
      + 0.15 * (1 - criminalita_norm)
      + 0.10 * (1 - polarizzazione_norm)     # P90/P10 ratio
```

Pesi configurabili dall'utente in dashboard tramite slider, ranking ricalcolato client-side.

### Reddito sostenibile

```
RS(comune, profilo) = paniere_ISTAT_regionale(profilo)
                    + 12 × affitto_medio_OMI_zona_centrale(comune)
                    + IRPEF_e_addizionali(reddito_lordo_eq, profilo)
```

Profili: N adulti (1-2), N figli minorenni (0-3+), N percettori di reddito (1-2), modalità abitazione (affitto / mutuo / proprietà).

## Struttura repo

```
qualita-vita-italia/
├── dati/
│   ├── raw/           cache scaricati (gitignored)
│   └── processed/     CSV puliti (commitati)
├── script/
│   ├── 01_download.py            download incrementale tutte le fonti
│   ├── 02_etl_redditi.py         fasce MEF → quartili+decili per comune
│   ├── 03_etl_omi.py             parser OMI open data semestrale
│   ├── 04_etl_costi.py           indice costo regionale ISTAT IPC
│   ├── 05_etl_criminalita.py     ISTAT delittuosità provinciale
│   ├── 06_etl_servizi.py         ISTAT BES provinciale
│   ├── 07_join_indicatori.py     tabella unica + indice qualità
│   ├── 08_build_dashboard.py     HTML standalone vista nazionale
│   ├── 09_build_classifica.py    pagina top 100 / bottom 100
│   └── run_pipeline.sh
├── output/
│   ├── dashboard.html            vista nazionale (no OMI)
│   ├── classifica.html           top/bottom 100 stand-alone
│   ├── wordpress.html            snippet WP page-macro
│   └── data/
│       ├── comuni.json           payload completo dashboard
│       ├── classifica.json       ranking pre-calcolato
│       └── comuni_geo.geojson    geometrie semplificate
└── wp-integration/
    ├── omi-fetcher.php           fetcher OMI server-side
    ├── reddito-sostenibile.js    calcolatore client-side
    └── dettaglio-comune.js       pannello comune con OMI lazy
```

## Avvertenze metodologiche

- I percentili (P10, Q1, mediana, Q3, P90) sono **stime per interpolazione lineare nelle 8 fasce MEF**, non micro-dati. La fascia top (>120k) è aperta: P95/P99 non ricavabili.
- Comuni con <500 contribuenti hanno stime rumorose, etichettati con flag visivo.
- Criminalità e servizi sono **provinciali**, applicati ai comuni della provincia (i grandi comuni hanno integrazione Min. Interno).
- L'indice costo vita per i comuni minori è un proxy ISTAT IPC regionale + affitto OMI locale, non un dato Numbeo (Numbeo copre solo ~30 grandi città).

## Setup

```bash
git clone https://github.com/DrElegantia/qualita-vita-italia.git
cd qualita-vita-italia
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
./script/run_pipeline.sh
```

## Pubblicazione su umbertobertonelli.it

Pagina WordPress: `/macro/dove-vivere/` (template macro). Vedi `wp-integration/` per OMI fetcher PHP, JS calcolatore reddito sostenibile, JS pannello dettaglio comune.

## Licenza

MIT (codice). I dati restano sotto le rispettive licenze (MEF: CC-BY 4.0, ISTAT: CC-BY 3.0 IT, OMI Agenzia Entrate: open data).
