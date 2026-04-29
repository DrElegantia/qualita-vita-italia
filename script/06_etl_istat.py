#!/usr/bin/env python3
"""
ETL ISTAT: scarica e parsea via SDMX REST API esploradati.istat.it
- BES_TERRIT: indici sintetici di benessere territoriale (provincia/regione)
- 73_67_DF_DCCV_DELITTIPS: delittuosita per provincia (tasso per 10k abitanti)

Output:
- dati/processed/istat_delitti_provinciale.csv: tasso delitti totale per provincia, ultimo anno
- dati/processed/istat_bes_provinciale.csv: BES indicatori sintetici per provincia/regione

Note:
- SDMX endpoint nuovo (post-migrazione I.STAT 2025): https://esploradati.istat.it/SDMXWS
- Rate limit: 5 query/minuto per IP. Throttle 13s tra request.
- SDMX-JSON schema 2.0.0.
"""
from __future__ import annotations
import csv
import json
import logging
import sys
import time
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("etl_istat")

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "dati" / "raw" / "istat"
RAW.mkdir(parents=True, exist_ok=True)
PROC = ROOT / "dati" / "processed"

SDMX_BASE = "https://esploradati.istat.it/SDMXWS/rest"
THROTTLE_S = 13


def http_get_json(url: str, dest: Path | None = None) -> dict:
    """GET con throttle 13s + cache su disco."""
    if dest and dest.exists():
        log.info("cache hit: %s", dest)
        return json.loads(dest.read_text())
    log.info("fetch %s", url)
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.sdmx.data+json;version=2.0.0"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8"))
    finally:
        time.sleep(THROTTLE_S)
    if dest:
        dest.write_text(json.dumps(data, ensure_ascii=False))
    return data


def parse_sdmx_json(data: dict) -> tuple[list[dict], list[str]]:
    """SDMX-JSON 2.0.0 → list of {dim_id: code, OBS_VALUE: float, ...}.
    Ogni observation ha una key tipo "0:0:1:0" che indice le dimensioni.
    """
    structure = data["data"]["structures"][0]
    dim_obs = structure["dimensions"]["observation"]
    # Ogni dimensione ha {id, keyPosition, values:[{id, name},...]}
    dim_specs = sorted(dim_obs, key=lambda d: d["keyPosition"])
    dim_ids = [d["id"] for d in dim_specs]
    dim_values = [d["values"] for d in dim_specs]

    # Attribute: alcuni dataset hanno OBS_VALUE come unico componente nei [value, ...] dell'observation
    # ma potrebbero esserci anche attributes (es. unit_measure, status). Skip per ora.
    rows = []
    for ds in data["data"]["dataSets"]:
        obs = ds.get("observations", {})
        for key, vals in obs.items():
            indices = [int(x) for x in key.split(":")]
            row = {}
            for i, idx in enumerate(indices):
                code = dim_values[i][idx]["id"]
                row[dim_ids[i]] = code
            row["OBS_VALUE"] = vals[0] if vals and len(vals) > 0 else None
            rows.append(row)
    return rows, dim_ids


# Mapping NUTS-2 ISTAT (codifica pre-2008 usata da BES_TERRIT) → nome regione MEF
NUTS2_TO_REGIONE = {
    # Nord-Ovest (ITC)
    "ITC1": "Piemonte",
    "ITC2": "Valle d'Aosta",
    "ITC20": "Valle d'Aosta",
    "ITC3": "Liguria",
    "ITC4": "Lombardia",
    # Nord-Est (ITD, codifica vecchia)
    "ITD10": "Trentino-Alto Adige",  # P.A. Bolzano
    "ITD20": "Trentino-Alto Adige",  # P.A. Trento
    "ITDA":  "Trentino-Alto Adige",  # macro Trentino
    "ITD3":  "Veneto",
    "ITD4":  "Friuli-Venezia Giulia",
    "ITD5":  "Emilia-Romagna",
    # Centro (ITE, codifica vecchia)
    "ITE1": "Toscana",
    "ITE2": "Umbria",
    "ITE3": "Marche",
    "ITE4": "Lazio",
    # Sud (ITF)
    "ITF1": "Abruzzo",
    "ITF2": "Molise",
    "ITF3": "Campania",
    "ITF4": "Puglia",
    "ITF5": "Basilicata",
    "ITF6": "Calabria",
    # Isole (ITG)
    "ITG1": "Sicilia",
    "ITG2": "Sardegna",
}

# Indicatori BES con copertura regionale piena (verificati su edizione 2025).
# Ogni indicatore ha verso "+" (piu alto = meglio) o "-" (piu basso = meglio).
BES_INDICATORI = {
    "01SAL001":     ("salute_speranza_vita", "+"),
    "02IST002-N22": ("istruzione_secondaria", "+"),
    "02IST003P-N22":("istruzione_terziaria", "+"),
    "02IST006-N22": ("istruzione_neet", "-"),
    "03LAV001-N22": ("lavoro_tasso_occupazione", "+"),
    "03LAV002-N22": ("lavoro_non_partecipazione", "-"),
    "06POL001":     ("politica_affluenza", "+"),
    "12SER020":     ("servizi_banda_larga", "+"),
}


def aggregate_delitti(rows: list[dict]) -> dict[str, dict]:
    """Aggrega DELITTIPS per REF_AREA (codice ISTAT capoluogo).
    Output: {codice_istat: {totale_delitti, anno}}."""
    # Filtro ultimo anno disponibile
    years = sorted({r["TIME_PERIOD"] for r in rows if r.get("TIME_PERIOD")})
    last_year = years[-1] if years else None
    log.info("anni delittuosita disponibili: %s (uso %s)", years, last_year)

    # Tipologie generali da sommare. Escludo categorie totali per evitare doppi conteggi.
    # Nei dati ISTAT le TYPE_CRIME sono in genere tutte mutuamente esclusive a partire da
    # codici come ARSON, BAGTHEF, BURGTHEF, etc. C'e' anche un codice "TOTAL" che rappresenta
    # la somma — se presente lo uso direttamente.
    type_crimes = {r["TYPE_CRIME"] for r in rows}
    log.info("tipologie crimini distinte: %d (%s...)", len(type_crimes), sorted(type_crimes)[:5])

    aggregated: dict[str, dict] = {}
    use_total = "TOTAL" in type_crimes
    target_types = {"TOTAL"} if use_total else type_crimes

    for r in rows:
        if r.get("TIME_PERIOD") != last_year:
            continue
        if r.get("TYPE_CRIME") not in target_types:
            continue
        cod = r.get("REF_AREA", "").zfill(6)
        try:
            v = float(r.get("OBS_VALUE") or 0)
        except (ValueError, TypeError):
            continue
        if cod not in aggregated:
            aggregated[cod] = {"totale_delitti": 0.0, "anno": int(last_year)}
        aggregated[cod]["totale_delitti"] += v
    log.info("REF_AREA capoluoghi con dato: %d", len(aggregated))
    return aggregated


def aggregate_bes(rows: list[dict]) -> dict[str, dict]:
    """Aggrega BES_TERRIT per REF_AREA (NUTS-2 regione), prendendo l'ultimo anno
    disponibile per ogni (regione, indicatore). SEX=T (totale).
    Per Trentino-Alto Adige media le due Province Autonome (ITH1+ITH2)."""
    # Costruisco dict {(regione, label): {anno: valore}}
    raw = {}
    for r in rows:
        if r.get("SEX") != "T":
            continue
        nuts = r.get("REF_AREA", "")
        if nuts not in NUTS2_TO_REGIONE:
            continue
        data_type = r.get("DATA_TYPE", "")
        if data_type not in BES_INDICATORI:
            continue
        label, _verso = BES_INDICATORI[data_type]
        try:
            v = float(r.get("OBS_VALUE") or 0)
        except (ValueError, TypeError):
            continue
        anno = r.get("TIME_PERIOD")
        regione = NUTS2_TO_REGIONE[nuts]
        raw.setdefault((regione, label, nuts), {})[anno] = v

    # Per ogni (regione, label, nuts), prendo l'anno piu recente
    by_reg_label: dict[tuple[str, str], list[float]] = {}
    for (reg, lbl, nuts), per_year in raw.items():
        last = max(per_year.keys())
        by_reg_label.setdefault((reg, lbl), []).append(per_year[last])

    # Per Trentino-AA: media i due NUTS (ITH1+ITH2). Per le altre regioni 1 valore.
    out: dict[str, dict] = {}
    for (reg, lbl), vals in by_reg_label.items():
        out.setdefault(reg, {})[lbl] = sum(vals) / len(vals)

    log.info("regioni BES con dato: %d / 20 (Trentino-AA mediato tra Bolzano e Trento)", len(out))
    return out


def fetch_delitti() -> dict[str, dict]:
    url = (f"{SDMX_BASE}/data/IT1,73_67_DF_DCCV_DELITTIPS_1,1.0/all/ALL/"
           f"?format=jsondata&dimensionAtObservation=AllDimensions&startPeriod=2022")
    cache = RAW / "delittips_1.json"
    data = http_get_json(url, cache)
    rows, _ = parse_sdmx_json(data)
    return aggregate_delitti(rows)


def fetch_bes() -> dict[str, dict]:
    url = (f"{SDMX_BASE}/data/IT1,DF_BES_TERRIT,1.0/all/ALL/"
           f"?format=jsondata&dimensionAtObservation=AllDimensions&startPeriod=2022")
    cache = RAW / "bes_territ.json"
    data = http_get_json(url, cache)
    rows, _ = parse_sdmx_json(data)
    return aggregate_bes(rows)


def write_delitti_csv(agg: dict[str, dict]) -> Path:
    out = PROC / "istat_delitti_capoluoghi.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["codice_istat", "totale_delitti", "anno"])
        for cod, d in sorted(agg.items()):
            w.writerow([cod, int(d["totale_delitti"]), d["anno"]])
    log.info("scritto %s (%d righe)", out, len(agg))
    return out


def write_bes_csv(agg: dict[str, dict]) -> Path:
    out = PROC / "istat_bes_regionale.csv"
    indicatori = sorted({lbl for d in agg.values() for lbl in d.keys()})
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["regione"] + indicatori)
        for reg, d in sorted(agg.items()):
            w.writerow([reg] + [d.get(lbl, "") for lbl in indicatori])
    log.info("scritto %s (%d regioni × %d indicatori)", out, len(agg), len(indicatori))
    return out


def main() -> int:
    log.info("=== Delittuosita capoluoghi ===")
    delitti = fetch_delitti()
    write_delitti_csv(delitti)

    log.info("=== BES regionale ===")
    bes = fetch_bes()
    write_bes_csv(bes)

    return 0


if __name__ == "__main__":
    sys.exit(main())
