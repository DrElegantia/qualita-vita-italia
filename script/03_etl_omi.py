#!/usr/bin/env python3
"""
ETL OMI: scarica le quotazioni immobiliari per tutti i comuni italiani via
API GEOPOI (Agenzia delle Entrate) e produce un valore aggregato per comune.

Approccio: scrape dei medesimi endpoint pubblici che alimentano la
consultazione web (https://wwwt.agenziaentrate.gov.it/geopoi_omi/index.htm).

Nessuna autenticazione richiesta. Throttle 200ms per non sovraccaricare
il server. Cache su disco per ogni comune (riusabile tra run).

Output:
- dati/processed/omi_comuni.csv: per ogni comune (codice ISTAT match):
    codice_istat, codcom_omi, sigla_provincia, comune, n_zone,
    prezzo_acq_eur_mq_min, prezzo_acq_eur_mq_max, prezzo_acq_eur_mq_med,
    affitto_eur_mq_mese_min, affitto_eur_mq_mese_max, affitto_eur_mq_mese_med,
    semestre, fascia_centrale_disponibile

Aggrega su tipologia "Abitazioni civili" + "Abitazioni di tipo economico"
(le piu rappresentative per "vivere bene"), stato di conservazione "normale".

Modalita di run:
- python3 03_etl_omi.py --comuni 20         # test su primi 20 comuni
- python3 03_etl_omi.py --province MI,RM    # solo province specifiche
- python3 03_etl_omi.py                     # bulk: tutti i ~7900 comuni
- python3 03_etl_omi.py --resume            # riprende da cache esistente
"""

from __future__ import annotations
import argparse
import csv
import io
import json
import logging
import re
import statistics
import sys
import time
import unicodedata
import urllib.request
import urllib.parse
import urllib.error
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("etl_omi")

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "dati" / "raw"
RAW_OMI = RAW / "omi"
CACHE_DIR = RAW_OMI / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
PROC = ROOT / "dati" / "processed"

OMI_HOST = "https://www1.agenziaentrate.gov.it/servizi/geopoi_omi"
REFERER = "https://wwwt.agenziaentrate.gov.it/geopoi_omi/index.htm"
USER_AGENT = "qualita-vita-italia-bot/0.1 (+https://umbertobertonelli.it; ricerca dati pubblici OMI)"
THROTTLE_MS = 200
TIMEOUT_S = 20

# Tipologie da aggregare: residenziale "vivere bene"
TIPOLOGIE_KEEP = {
    "abitazioni civili",
    "abitazioni di tipo economico",
}
STATO_KEEP = "normale"

# Alias province (l'API GEOPOI usa sigle pre-2009)
PROV_ALIAS = {
    "BT": ["BA", "FG"],
    "FC": ["FO"],
    "FM": ["AP"],
    "MB": ["MI"],
    "PU": ["PS"],
    "SU": ["CA", "NU"],
    "CA": ["NU"],
    "OR": ["NU"],
    "SS": ["NU"],
}


# =============================================================================
# HTTP helper con throttle
# =============================================================================
_last_request = [0.0]


def http_get(url: str, expect_json: bool = True, retry: int = 2) -> dict | str | None:
    """GET con throttle 200ms, header Referer corretti, e retry con backoff
    su errori DNS/transient (urlopen errno 8, timeout)."""
    for attempt in range(retry + 1):
        elapsed_ms = (time.time() - _last_request[0]) * 1000
        if elapsed_ms < THROTTLE_MS:
            time.sleep((THROTTLE_MS - elapsed_ms) / 1000)
        _last_request[0] = time.time()

        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Referer": REFERER,
            "Accept": "application/json, */*" if expect_json else "text/html, */*",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                body = r.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < retry:
                time.sleep(2 ** attempt)
                continue
            log.warning("HTTP %d on %s", e.code, url)
            return None
        except Exception as e:
            if attempt < retry:
                time.sleep(2 ** attempt + 1)
                continue
            log.warning("error %s on %s", e, url)
            return None

    if expect_json:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            log.warning("JSON parse error: %s", url)
            return None
    return body


# =============================================================================
# Step 1: ultimo semestre disponibile
# =============================================================================
def fetch_ultimo_semestre() -> str | None:
    cache = CACHE_DIR / "ultimo_semestre.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 86400:
        return json.loads(cache.read_text())["semestre"]
    data = http_get(f"{OMI_HOST}/zoneomi.php?richiesta=5")
    if not isinstance(data, list):
        return None
    sems = sorted({d.get("SEMESTRE") for d in data if d.get("SEMESTRE")}, reverse=True)
    if not sems:
        return None
    cache.write_text(json.dumps({"semestre": sems[0], "fetched_at": time.time()}))
    return sems[0]


# =============================================================================
# Step 2: mapping nome comune → CODCOM per provincia
# =============================================================================
def fetch_codcom_provincia(prov: str) -> dict[str, str]:
    """Map normalized_name -> CODCOM per una provincia. Cached su disco."""
    cache = CACHE_DIR / f"codcom_{prov}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    data = http_get(f"{OMI_HOST}/zoneomi.php?richiesta=2&prov={urllib.parse.quote(prov)}")
    if not isinstance(data, list):
        return {}
    out = {}
    for row in data:
        name = (row.get("DIZIONE") or "").strip()
        code = (row.get("CODCOM") or "").strip()
        if name and code:
            out[fold_comune(name)] = code
    cache.write_text(json.dumps(out, ensure_ascii=False))
    return out


def fold_comune(s: str) -> str:
    """Normalizzazione toponimi compatibile con API GEOPOI.
    Replica gb_omi_fold_comune del tema geometra-bertonelli (PHP):
    rimuove apostrofi, dash unicode, accenti; sostituisce j arcaica con i."""
    s = re.sub(r"['‘’`´ʼʻ]", "", s)
    s = re.sub(r"[‐-―\-]+", " ", s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.upper().replace("J", "I")
    return s


def _candidate_names(comune_nome: str) -> list[str]:
    """Genera varianti del nome per il match con API GEOPOI.
    Gestisce:
    - nome bilingue MEF "BOLZANO .BOZEN." → prova prima "BOLZANO"
    - nome con slash "TRENTO/TRIENT" → prova "TRENTO" (raro)
    - nome con parentesi "..(SO).." → strip parentetiche
    """
    out = [comune_nome]
    # Stacca tutto dal primo " ." in poi (formato bilingue altoatesino)
    if " ." in comune_nome:
        out.append(comune_nome.split(" .")[0].strip())
    # Stacca da /
    if "/" in comune_nome:
        out.append(comune_nome.split("/")[0].strip())
    # Rimuovi parentetiche "(BZ)" o "(BZ-Bolzano)"
    if "(" in comune_nome:
        out.append(re.sub(r"\s*\(.*?\)", "", comune_nome).strip())
    seen, uniq = set(), []
    for n in out:
        if n and n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def find_codcom(prov: str, comune_nome: str) -> str | None:
    """Cerca CODCOM con doppio fallback: nome alternativo + provincia alias."""
    candidates = _candidate_names(comune_nome)
    for cand in candidates:
        target = fold_comune(cand)
        for p in [prov] + PROV_ALIAS.get(prov, []):
            m = fetch_codcom_provincia(p)
            if target in m:
                return m[target]
    return None


# =============================================================================
# Step 3: zone OMI per un comune (lista codici)
# =============================================================================
def fetch_zone_comune(codcom: str, semestre: str) -> list[str]:
    cache = CACHE_DIR / f"zone_{codcom}_{semestre}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    data = http_get(f"{OMI_HOST}/zoneomi.php?richiesta=6&codcom={codcom}&semestre={semestre}")
    if not data:
        return []
    features = data.get("dat", {}).get("features", []) or data.get("features", [])
    zone = sorted({f.get("properties", {}).get("zona") for f in features if f.get("properties", {}).get("zona")})
    zone = [z for z in zone if z]
    cache.write_text(json.dumps(zone))
    return zone


# =============================================================================
# Step 4: link_zona per stampaomi
# =============================================================================
def fetch_link_zona(codcom: str, semestre: str, zona: str) -> str | None:
    data = http_get(f"{OMI_HOST}/zoneomi.php?richiesta=8&codcom={codcom}&semestre={semestre}&zo={urllib.parse.quote(zona)}")
    if not isinstance(data, list) or not data:
        return None
    for t in data:
        link = t.get("LINK_ZONA")
        if link:
            return link
    return None


# =============================================================================
# Step 5: parser HTML stampaomi.php → righe (tipologia, stato, min, max)
# =============================================================================
class _OMITableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_td = False
        self.cur_row = []
        self.cur_cell = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            self.in_td = True
            self.cur_cell = []
        elif tag == "tr":
            self.cur_row = []

    def handle_endtag(self, tag):
        if tag == "td":
            self.cur_row.append("".join(self.cur_cell).strip())
            self.in_td = False
        elif tag == "tr":
            if self.cur_row:
                self.rows.append(self.cur_row)
            self.cur_row = []

    def handle_data(self, data):
        if self.in_td:
            self.cur_cell.append(data)


def parse_num_it(s: str) -> float:
    """Numero formato italiano: '1.234,56' -> 1234.56. NBSP/spazi rimossi."""
    s = re.sub(r"[ \s]+", "", s)
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def fetch_valori_zona(codcom: str, link_zona: str, semestre: str, tipo_lettera: str) -> list[dict]:
    """tipo_lettera: 'R' residenziale, 'C' commerciale, 'T' terziario.

    La tabella HTML ha 8 colonne dati per riga:
      0 tipologia, 1 stato_conservativo,
      2 compravendita_min (eur/mq), 3 compravendita_max,
      4 superficie L/N (compravendita),
      5 locazione_min (eur/mq mese), 6 locazione_max,
      7 superficie L/N (locazione).

    Locazione e' opzionale: alcune righe hanno solo compravendita (4 colonne effettive).
    Output: [{tipologia, stato, acq_min, acq_max, loc_min, loc_max}].
    """
    url = f"{OMI_HOST}/stampaomi.php?{codcom}/{link_zona}/{semestre}/{tipo_lettera}/0/0/0"
    body = http_get(url, expect_json=False)
    if not body:
        return []
    p = _OMITableParser()
    p.feed(body)
    out = []
    for r in p.rows:
        if len(r) < 4:
            continue
        tipologia = r[0].strip()
        stato = r[1].strip().lower()
        if stato not in {"ottimo", "normale", "scadente"}:
            continue
        acq_min = parse_num_it(r[2])
        acq_max = parse_num_it(r[3])
        # Locazione disponibile se ci sono almeno 7 colonne
        loc_min = parse_num_it(r[5]) if len(r) >= 7 else 0.0
        loc_max = parse_num_it(r[6]) if len(r) >= 7 else 0.0
        if acq_min <= 0 and loc_min <= 0:
            continue
        out.append({
            "tipologia": tipologia,
            "stato": stato,
            "acq_min": acq_min if acq_min > 0 else None,
            "acq_max": acq_max if acq_max > 0 else None,
            "loc_min": loc_min if loc_min > 0 else None,
            "loc_max": loc_max if loc_max > 0 else None,
        })
    return out


# =============================================================================
# Aggregator: dati per zona (residenziale R, tutte le tipologie)
# =============================================================================
def fetch_zona_dati(codcom: str, zona: str, semestre: str) -> dict:
    """Ritorna {tipologie: [{tipologia, acq_min, acq_max, loc_min, loc_max}]}
    per residenziale R, stato 'normale'."""
    cache = CACHE_DIR / f"valori_{codcom}_{zona}_{semestre}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    link = fetch_link_zona(codcom, semestre, zona)
    if not link:
        out = {"tipologie": []}
        cache.write_text(json.dumps(out))
        return out
    righe = fetch_valori_zona(codcom, link, semestre, "R")
    out_tip = []
    for r in righe:
        if r["stato"] != STATO_KEEP:
            continue
        out_tip.append({
            "tipologia_norm": fold_comune(r["tipologia"]),
            "tipologia": r["tipologia"],
            "acq_min": r["acq_min"],
            "acq_max": r["acq_max"],
            "loc_min": r["loc_min"],
            "loc_max": r["loc_max"],
        })
    out = {"tipologie": out_tip, "link_zona": link}
    cache.write_text(json.dumps(out, ensure_ascii=False))
    return out


# =============================================================================
# Pipeline per comune
# =============================================================================
def aggregate_comune(prov: str, codice_istat: str, comune_nome: str, semestre: str) -> dict | None:
    """Per un comune produce il record aggregato sui valori residenziali.
    Aggregazione: mediana dei punti centrali min-max sulle zone OMI del comune,
    limitato alle tipologie 'Abitazioni civili' e 'Abitazioni di tipo economico',
    stato 'normale'."""
    codcom = find_codcom(prov, comune_nome)
    if not codcom:
        return None
    zone = fetch_zone_comune(codcom, semestre)
    if not zone:
        return None
    acq_centers, acq_mins, acq_maxs = [], [], []
    loc_centers, loc_mins, loc_maxs = [], [], []
    fascia_centrale = False
    n_zone_dati = 0
    for z in zone:
        if z and z[0].upper() == "B":
            fascia_centrale = True
        d = fetch_zona_dati(codcom, z, semestre)
        zona_ha_dati = False
        for t in d.get("tipologie", []):
            tipo_norm = t["tipologia_norm"].lower()
            if not any(k in tipo_norm for k in ["abitazioni civili", "abitazioni di tipo economico"]):
                continue
            if t.get("acq_min") and t.get("acq_max"):
                acq_mins.append(t["acq_min"])
                acq_maxs.append(t["acq_max"])
                acq_centers.append((t["acq_min"] + t["acq_max"]) / 2)
                zona_ha_dati = True
            if t.get("loc_min") and t.get("loc_max"):
                loc_mins.append(t["loc_min"])
                loc_maxs.append(t["loc_max"])
                loc_centers.append((t["loc_min"] + t["loc_max"]) / 2)
                zona_ha_dati = True
        if zona_ha_dati:
            n_zone_dati += 1
    if not (acq_centers or loc_centers):
        return None

    def med_or_none(xs: list[float]) -> float | None:
        return round(statistics.median(xs), 2) if xs else None

    return {
        "codice_istat": codice_istat,
        "codcom_omi": codcom,
        "sigla_provincia": prov,
        "comune": comune_nome,
        "n_zone": len(zone),
        "n_zone_con_dati": n_zone_dati,
        "prezzo_acq_eur_mq_min": med_or_none(acq_mins),
        "prezzo_acq_eur_mq_max": med_or_none(acq_maxs),
        "prezzo_acq_eur_mq_med": med_or_none(acq_centers),
        "affitto_eur_mq_mese_min": med_or_none(loc_mins),
        "affitto_eur_mq_mese_max": med_or_none(loc_maxs),
        "affitto_eur_mq_mese_med": med_or_none(loc_centers),
        "semestre": semestre,
        "fascia_centrale_disponibile": fascia_centrale,
    }


# =============================================================================
# Main
# =============================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comuni", type=int, default=0, help="N comuni di test (0 = tutti)")
    ap.add_argument("--province", type=str, default="", help="Sigle provincia separate da virgola")
    ap.add_argument("--resume", action="store_true", help="Riprende da cache, salta comuni gia fatti")
    args = ap.parse_args()

    semestre = fetch_ultimo_semestre()
    if not semestre:
        log.error("API OMI non raggiungibile")
        return 1
    log.info("ultimo semestre OMI: %s", semestre)

    src = PROC / "comuni_redditi_ultimo.csv"
    if not src.exists():
        log.error("manca %s — esegui 02_etl_redditi.py prima", src)
        return 1
    # keep_default_na=False per evitare che pandas interpreti "NA" (Napoli) come NaN
    df = pd.read_csv(src, dtype={"codice_istat": str, "sigla_provincia": str},
                     keep_default_na=False, na_values=[""])

    if args.province:
        provs = [p.strip().upper() for p in args.province.split(",")]
        df = df[df["sigla_provincia"].isin(provs)]
    if args.comuni:
        # Top N per n_contribuenti (focus capoluoghi e citta grandi)
        df = df.nlargest(args.comuni, "n_contribuenti")

    log.info("comuni in target: %d", len(df))

    rows = []
    fail = 0
    out_path = PROC / "omi_comuni.csv"
    for idx, row in enumerate(df.itertuples(index=False), 1):
        prov = row.sigla_provincia
        nome = row.comune
        cod = row.codice_istat
        try:
            rec = aggregate_comune(prov, cod, nome, semestre)
        except Exception as e:
            log.warning("err %s/%s: %s", prov, nome, e)
            rec = None
        if rec:
            rows.append(rec)
        else:
            fail += 1
        if idx % 50 == 0:
            log.info("%d/%d processati (%d ok, %d fail)", idx, len(df), len(rows), fail)
            # checkpoint
            pd.DataFrame(rows).to_csv(out_path, index=False)

    df_out = pd.DataFrame(rows)
    df_out.to_csv(out_path, index=False)
    log.info("scritto %s (%d righe)", out_path, len(df_out))
    log.info("fail: %d (%.1f%%)", fail, 100 * fail / max(len(df), 1))

    if not df_out.empty:
        print("\n=== Sanity check ===")
        print(f"Comuni con dati OMI: {len(df_out)}")
        print(f"Comuni con fascia centrale (B*): {int(df_out['fascia_centrale_disponibile'].sum())}")
        print(f"Mediana nazionale prezzo acq: {df_out['prezzo_acq_eur_mq_med'].median():.0f} €/mq")
        print(f"Mediana nazionale affitto:   {df_out['affitto_eur_mq_mese_med'].median():.2f} €/mq mese")
        print("\nTop 10 comuni per prezzo acquisto:")
        cols = ["comune", "sigla_provincia", "n_zone", "prezzo_acq_eur_mq_med", "affitto_eur_mq_mese_med"]
        print(df_out.nlargest(10, "prezzo_acq_eur_mq_med")[cols].to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
