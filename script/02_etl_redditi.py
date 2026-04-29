#!/usr/bin/env python3
"""
ETL MEF: produce per ogni comune un set ricco di indicatori reddito,
inclusi P10, Q1, mediana, Q3, P90 stimati per interpolazione lineare
nelle 8 fasce di reddito complessivo pubblicate dal MEF.

Output in dati/processed/:
- comuni_redditi.csv          per ogni comune e anno: medio, percentili, percentuali
- comuni_redditi_ultimo.csv   solo l'anno piu recente (vista mappa)
- nazionale_redditi.csv       aggregato nazionale per anno (per sanity check)
- fasce_nazionali.csv         distribuzione nazionale per fascia (audit)

Schema fasce MEF (sempre 8):
  neg        Reddito complessivo <= 0
  0_10k      0 - 10.000
  10_15k     10.000 - 15.000
  15_26k     15.000 - 26.000
  26_55k     26.000 - 55.000
  55_75k     55.000 - 75.000
  75_120k    75.000 - 120.000
  over_120k  > 120.000  (fascia aperta, capped a 250.000 per stima percentile)

Metodologia percentili:
  Si esclude la fascia "neg" dal denominatore (impresa in perdita / no-percettori).
  Per il percentile q ∈ {0.10, 0.25, 0.50, 0.75, 0.90}:
    1) target = q × N (N = totale frequenze nelle 7 fasce positive)
    2) si cumulano le frequenze in ordine crescente di reddito
    3) trovata la fascia che contiene il target, si interpola linearmente:
         P_q = lo + (target - cum_prev) / freq_fascia × (hi - lo)

Limitazioni note:
  - La fascia top e aperta. Per i comuni con >10% sopra 120k (~30 capoluoghi
    benestanti) il P90 cade nella fascia top: la stima dipende dal cap (250k).
    Documentato come limite metodologico.
  - Comuni con <500 contribuenti hanno percentili rumorosi, etichettati
    con flag_rumoroso=True per visualizzazione.
"""

from __future__ import annotations
import csv
import io
import json
import logging
import sys
import zipfile
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("etl_redditi")

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "dati" / "raw"
RAW_MEF = RAW / "mef"
PROC = ROOT / "dati" / "processed"
PROC.mkdir(parents=True, exist_ok=True)

# (chiave, prefisso colonna MEF, lo, hi)
# La fascia neg e' esclusa dal calcolo dei percentili (ma e' contata nelle aggregate).
BRACKETS = [
    ("neg",       "Reddito complessivo minore o uguale a zero euro",     -10_000,  0),
    ("0_10k",     "Reddito complessivo da 0 a 10000 euro",                     0, 10_000),
    ("10_15k",    "Reddito complessivo da 10000 a 15000 euro",            10_000, 15_000),
    ("15_26k",    "Reddito complessivo da 15000 a 26000 euro",            15_000, 26_000),
    ("26_55k",    "Reddito complessivo da 26000 a 55000 euro",            26_000, 55_000),
    ("55_75k",    "Reddito complessivo da 55000 a 75000 euro",            55_000, 75_000),
    ("75_120k",   "Reddito complessivo da 75000 a 120000 euro",           75_000, 120_000),
    # Fascia aperta. Cap a 250k per interpolazione: scelta editoriale,
    # influenza solo i comuni con >10% in questa fascia (30-50 capoluoghi).
    ("over_120k", "Reddito complessivo oltre 120000 euro",               120_000, 250_000),
]
BRACKETS_POSITIVE = [b for b in BRACKETS if b[0] != "neg"]
PERCENTILES = [("p10", 0.10), ("q1", 0.25), ("mediana", 0.50), ("q3", 0.75), ("p90", 0.90)]
SOGLIA_RUMOROSO = 500  # n_contribuenti

REGIONE_MEF_TO_CANONICAL = {
    "Emilia Romagna": "Emilia-Romagna",
    "Friuli Venezia Giulia": "Friuli-Venezia Giulia",
    "Trentino Alto Adige": "Trentino-Alto Adige",
    "Trentino Alto Adige(P.A.Trento)": "Trentino-Alto Adige",
    "Trentino Alto Adige(P.A.Bolzano)": "Trentino-Alto Adige",
}
REGIONI_INVALIDE = {"Mancante/errata", "Non indicato", ""}


def normalize_regione(name: object) -> str | None:
    if not isinstance(name, str):
        return None
    s = name.strip()
    if s in REGIONI_INVALIDE:
        return None
    return REGIONE_MEF_TO_CANONICAL.get(s, s)


def open_mef_csv(year: int) -> pd.DataFrame:
    """Parser robusto: alcuni anni MEF hanno righe con trailing fields extra.
    Tronchiamo manualmente a len(header) campi per riga."""
    zpath = RAW_MEF / f"irpef_{year}.zip"
    with zipfile.ZipFile(zpath) as z:
        name = z.namelist()[0]
        with z.open(name) as f:
            data = f.read().decode("utf-8", errors="replace")
    text = data.replace("\r\n", "\n").rstrip("\n")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    rows = list(reader)
    header = [c.strip() for c in rows[0]]
    n = len(header)
    rows_clean = [
        r[:n] + [""] * (n - len(r)) if len(r) < n else r[:n]
        for r in rows[1:]
        if r
    ]
    return pd.DataFrame(rows_clean, columns=header)


def num(s: pd.Series) -> pd.Series:
    """Converte serie testuali (con virgola decimale italiana) in float, NaN→0."""
    return pd.to_numeric(s.astype(str).str.replace(",", ".", regex=False), errors="coerce").fillna(0)


def get_or_zero(df: pd.DataFrame, col: str) -> pd.Series:
    if col in df.columns:
        return num(df[col])
    return pd.Series([0] * len(df), index=df.index, dtype=float)


def quantile_from_brackets(freqs: list[float], q: float) -> float | None:
    """Interpolazione lineare nelle BRACKETS_POSITIVE.
    freqs: lista di frequenze nello stesso ordine di BRACKETS_POSITIVE.
    q ∈ (0, 1). Ritorna None se totale frequenze == 0."""
    total = sum(freqs)
    if total <= 0:
        return None
    target = q * total
    cum = 0.0
    for (_, _, lo, hi), f in zip(BRACKETS_POSITIVE, freqs, strict=True):
        if f <= 0:
            continue
        if cum + f >= target:
            in_bin = target - cum
            return lo + (in_bin / f) * (hi - lo)
        cum += f
    # Edge case: target oltre cum (succede solo per arrotondamenti). Ritorna estremo superiore.
    last_hi = BRACKETS_POSITIVE[-1][3]
    return float(last_hi)


CODICI_ISTAT_INVALIDI = {"", "0", "00000", "000000"}
COMUNI_INVALIDI = {"", "0", "non indicato", "n.d.", "nd"}


def compute_comuni(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Riga-per-riga (un comune) calcola gli indicatori reddito + percentili.
    Filtra il record fittizio MEF "redditi non attribuibili a comune"
    (codice_istat=0, comune='0' o 'Non indicato', ~5000 contribuenti per anno)."""
    cod = df["Codice Istat Comune"].astype(str).str.strip()
    nome = df["Denominazione Comune"].astype(str).str.strip()
    keep_mask = ~cod.isin(CODICI_ISTAT_INVALIDI) & ~nome.str.lower().isin(COMUNI_INVALIDI)
    df = df.loc[keep_mask].reset_index(drop=True)

    out = pd.DataFrame({
        "codice_istat": cod[keep_mask].reset_index(drop=True),
        "comune": nome[keep_mask].reset_index(drop=True),
        "sigla_provincia": df["Sigla Provincia"].astype(str).str.strip(),
        "regione": [normalize_regione(r) for r in df["Regione"].values],
    })
    out["anno"] = year

    out["n_contribuenti"] = num(df["Numero contribuenti"]).astype(int)
    imp_freq = num(df.get("Reddito imponibile - Frequenza", pd.Series([0] * len(df))))
    imp_amt = num(df.get("Reddito imponibile - Ammontare in euro", pd.Series([0] * len(df))))
    irpef = num(df.get("Imposta netta - Ammontare in euro", pd.Series([0] * len(df))))

    # Reddito complessivo: prefer colonna totale (anni recenti) o ricostruita dalle fasce
    if "Reddito complessivo - Frequenza" in df.columns:
        compl_freq = num(df["Reddito complessivo - Frequenza"])
        compl_amt = num(df["Reddito complessivo - Ammontare in euro"])
    else:
        compl_freq = sum(get_or_zero(df, f"{p} - Frequenza") for _, p, _, _ in BRACKETS)
        compl_amt = sum(get_or_zero(df, f"{p} - Ammontare in euro") for _, p, _, _ in BRACKETS)

    out["n_imponibile"] = imp_freq.astype(int)
    out["reddito_imponibile_medio"] = (imp_amt / imp_freq.replace(0, pd.NA)).round(0)
    out["reddito_complessivo_medio"] = (compl_amt / compl_freq.replace(0, pd.NA)).round(0)
    out["irpef_per_contribuente"] = (irpef / imp_freq.replace(0, pd.NA)).round(0)
    out["aliquota_effettiva_pct"] = ((irpef / imp_amt.replace(0, pd.NA)) * 100).round(2)

    # Frequenze per fascia (lista per riga)
    fasce_freq_cols = []
    for key, prefix, _lo, _hi in BRACKETS_POSITIVE:
        col = f"{prefix} - Frequenza"
        out[f"freq_{key}"] = get_or_zero(df, col).astype(int)
        fasce_freq_cols.append(f"freq_{key}")

    # Percentili per riga
    freqs_matrix = out[fasce_freq_cols].to_numpy()
    for label, q in PERCENTILES:
        vals = [quantile_from_brackets(list(row), q) for row in freqs_matrix]
        out[f"reddito_{label}"] = pd.Series(vals).round(0)

    # Percentuali aggregate (sotto 15k, sopra 55k, sopra 120k)
    pos_total = out[fasce_freq_cols].sum(axis=1).replace(0, pd.NA)
    bassi = out["freq_0_10k"] + out["freq_10_15k"]
    medi_alti = out["freq_55_75k"] + out["freq_75_120k"] + out["freq_over_120k"]
    over120 = out["freq_over_120k"]
    out["pct_sotto_15k"] = (bassi / pos_total * 100).round(2)
    out["pct_sopra_55k"] = (medi_alti / pos_total * 100).round(2)
    out["pct_sopra_120k"] = (over120 / pos_total * 100).round(2)

    # Indicatori derivati
    # Polarizzazione = % nei due estremi (sotto 15k + sopra 55k)
    out["polarizzazione"] = (out["pct_sotto_15k"] + out["pct_sopra_55k"]).round(2)
    # Ratio P90/P10: indice di disuguaglianza interna al comune (>0)
    p10_safe = out["reddito_p10"].replace(0, pd.NA)
    out["ratio_p90_p10"] = (out["reddito_p90"] / p10_safe).round(2)

    # Flag rumoroso: comuni piccoli con stime instabili
    out["flag_rumoroso"] = out["n_contribuenti"] < SOGLIA_RUMOROSO

    return out


def compute_nazionale(years_data: list[pd.DataFrame]) -> pd.DataFrame:
    """Aggregato nazionale: per ogni anno calcola reddito medio + percentili
    sommando le frequenze e gli ammontari di tutti i comuni."""
    rows = []
    for df in years_data:
        year = int(df["anno"].iloc[0])
        n_contrib = int(df["n_contribuenti"].sum())
        n_imp = int(df["n_imponibile"].sum())

        # Per il reddito medio nazionale serviamo gli ammontari (non sono nelle out comunali).
        # Ricalcolo dal raw del MEF per evitare propagazione errori
        # (vedi nota: il df qui passato e' gia per-comune, non e' il raw MEF).
        # Soluzione: somma frequenze per fascia e applica medie pesate dalle fasce.
        # Per F1 manteniamo solo medie pesate dalle frequenze comunali.

        # Aggregato fasce: somma freq per ogni fascia su tutti i comuni
        freq_per_bracket = []
        for key, _prefix, _lo, _hi in BRACKETS_POSITIVE:
            freq_per_bracket.append(int(df[f"freq_{key}"].sum()))

        row = {
            "year": year,
            "n_contribuenti": n_contrib,
            "n_imponibile": n_imp,
            "n_comuni": int(df["codice_istat"].nunique()),
        }
        # Percentili nazionali (interpolazione su distribuzione aggregata)
        for label, q in PERCENTILES:
            row[f"reddito_{label}"] = quantile_from_brackets(list(freq_per_bracket), q)

        # Reddito medio nazionale: somma pesata
        # Usa reddito_imponibile_medio comunale × n_imponibile / sum(n_imponibile)
        valid = df.dropna(subset=["reddito_imponibile_medio"])
        if valid["n_imponibile"].sum() > 0:
            row["reddito_imponibile_medio"] = round(
                (valid["reddito_imponibile_medio"] * valid["n_imponibile"]).sum()
                / valid["n_imponibile"].sum()
            )
        # Distribuzione aggregata
        for key, freq in zip([k for k, _, _, _ in BRACKETS_POSITIVE], freq_per_bracket, strict=True):
            row[f"freq_{key}"] = freq
        rows.append(row)
    return pd.DataFrame(rows).sort_values("year").reset_index(drop=True)


def fasce_nazionali(years_data: list[pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for df in years_data:
        year = int(df["anno"].iloc[0])
        for key, _prefix, lo, hi in BRACKETS_POSITIVE:
            rows.append({
                "year": year,
                "fascia": key,
                "lo_eur": lo,
                "hi_eur": hi,
                "freq": int(df[f"freq_{key}"].sum()),
            })
    return pd.DataFrame(rows)


def main() -> int:
    years = sorted(int(p.stem.split("_")[1]) for p in RAW_MEF.glob("irpef_*.zip"))
    if not years:
        log.error("nessun file MEF in %s — esegui prima 01_download.py", RAW_MEF)
        return 1
    log.info("anni MEF disponibili: %s", years)

    all_comuni = []
    for y in years:
        log.info("ETL anno %d", y)
        raw = open_mef_csv(y)
        log.info("  %d righe raw, %d colonne", len(raw), len(raw.columns))
        com = compute_comuni(raw, y)
        all_comuni.append(com)

    full = pd.concat(all_comuni, ignore_index=True)
    out_path = PROC / "comuni_redditi.csv"
    full.to_csv(out_path, index=False)
    log.info("scritto %s (%d righe, %d comuni × %d anni)",
             out_path, len(full), full["codice_istat"].nunique(), full["anno"].nunique())

    last_year = max(years)
    last = full[full["anno"] == last_year].copy()
    last_path = PROC / "comuni_redditi_ultimo.csv"
    last.to_csv(last_path, index=False)
    log.info("scritto %s (%d comuni, anno %d)", last_path, len(last), last_year)

    nat = compute_nazionale(all_comuni)
    nat_path = PROC / "nazionale_redditi.csv"
    nat.to_csv(nat_path, index=False)
    log.info("scritto %s (%d anni)", nat_path, len(nat))

    fasce = fasce_nazionali(all_comuni)
    fasce_path = PROC / "fasce_nazionali.csv"
    fasce.to_csv(fasce_path, index=False)
    log.info("scritto %s", fasce_path)

    # Sanity print
    print("\n=== Anno {} - sanity check ===".format(last_year))
    print("Comuni totali:", len(last))
    print("Comuni rumorosi (<500 contrib.):", int(last["flag_rumoroso"].sum()))
    print("\nReddito mediano nazionale:", nat[nat["year"] == last_year]["reddito_mediana"].iloc[0])
    print("Reddito P10 nazionale:", nat[nat["year"] == last_year]["reddito_p10"].iloc[0])
    print("Reddito P90 nazionale:", nat[nat["year"] == last_year]["reddito_p90"].iloc[0])

    print("\nTop 10 comuni per reddito mediano (>=500 contrib.):")
    big = last[~last["flag_rumoroso"]].copy()
    cols = ["comune", "sigla_provincia", "n_contribuenti", "reddito_mediana", "reddito_p90", "ratio_p90_p10"]
    print(big.sort_values("reddito_mediana", ascending=False).head(10)[cols].to_string(index=False))

    print("\nBottom 10 comuni per reddito mediano (>=500 contrib.):")
    print(big.sort_values("reddito_mediana", ascending=True).head(10)[cols].to_string(index=False))

    summary = {
        "anni": years,
        "anno_ultimo": last_year,
        "n_comuni_ultimo": len(last),
        "n_rumorosi_ultimo": int(last["flag_rumoroso"].sum()),
        "n_regioni": int(last["regione"].nunique()),
        "regioni_invalide": int(last["regione"].isna().sum()),
    }
    (PROC / "summary_redditi.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    log.info("summary scritto in %s", PROC / "summary_redditi.json")

    return 0


if __name__ == "__main__":
    sys.exit(main())
