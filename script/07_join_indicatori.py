#!/usr/bin/env python3
"""
Join indicatori + calcolo reddito sostenibile + indice qualita.

Produce, per ogni comune, una tabella unica con:
- redditi (P10/Q1/mediana/Q3/P90 + medio + percentuali fasce)
- prezzi/affitti OMI (se disponibili)
- reddito sostenibile per 5 profili familiari (single, coppia, coppia+1/2/3 figli)
- residuo annuo (mediana - reddito sostenibile) per ogni profilo
- punteggio qualita composito 0-100 (pesi default 60% residuo + 40% costo casa,
  estendibile con criminalita/servizi quando F3 sara disponibile)

Output:
- dati/processed/comuni_indicatori.csv: tabella completa
- output/data/comuni.json: payload per dashboard

Profili familiari pre-calcolati:
- single:        1 adulto, 0 figli, 1 percettore, 50 mq target
- coppia:        2 adulti, 0 figli, 1 percettore, 65 mq target
- coppia2:       2 adulti, 0 figli, 2 percettori, 65 mq target
- coppia_1f:     2 adulti, 1 figlio, 1 percettore, 75 mq target
- coppia_2f:     2 adulti, 2 figli, 2 percettori, 90 mq target

L'utente in dashboard puo' configurare profili custom via JS.

Note metodologiche:
- Paniere consumi: stima nazionale ISTAT consumi 2023 (esclusa casa) per profilo,
  multiplier regionale 1.0 (uniforme) finche' ISTAT IPC regionale non e' disponibile.
- IRPEF 2025: scaglioni 23% / 35% / 43%; detrazioni dipendente; addizionale
  regionale media 1,73% e comunale media 0,5%.
- Affitto annuo = 12 * mq * eur_mq_mese (locazione OMI mediana del comune).
- Mutuo modalita: surrogato rata = prezzo_acq * mq * 0,055 / 12 (30 anni, tasso 4,2%).
"""
from __future__ import annotations
import json
import logging
import sys
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("join")

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "dati" / "processed"
OUT_DATA = ROOT / "output" / "data"
OUT_DATA.mkdir(parents=True, exist_ok=True)

# =============================================================================
# Profili familiari
# =============================================================================
PROFILI = {
    "single":     {"adulti": 1, "figli": 0, "percettori": 1, "mq": 50,  "label": "1 adulto"},
    "coppia":     {"adulti": 2, "figli": 0, "percettori": 1, "mq": 65,  "label": "Coppia, 1 stipendio"},
    "coppia2":    {"adulti": 2, "figli": 0, "percettori": 2, "mq": 65,  "label": "Coppia, 2 stipendi"},
    "coppia_1f":  {"adulti": 2, "figli": 1, "percettori": 1, "mq": 75,  "label": "Coppia + 1 figlio, 1 stip."},
    "coppia_2f":  {"adulti": 2, "figli": 2, "percettori": 2, "mq": 90,  "label": "Coppia + 2 figli, 2 stip."},
}

# =============================================================================
# Paniere consumi nazionale (ISTAT 2023, escluso "Abitazione"). Cifre annue.
# Fonte: tabelle "Spesa media mensile delle famiglie" ISTAT, escluso voce "Abitazione".
# Saranno modulate per regione quando F3 (IPC regionale) sara disponibile.
# =============================================================================
PANIERE_NON_CASA = {
    "single":    8_400,    # alimentari + trasporti + utenze + servizi + tempo libero
    "coppia":   14_500,
    "coppia2":  14_500,
    "coppia_1f": 17_400,
    "coppia_2f": 21_000,
}

# =============================================================================
# IRPEF 2025
# =============================================================================
IRPEF_SCAGLIONI = [
    (28_000, 0.23),
    (50_000, 0.35),
    (float("inf"), 0.43),
]
NO_TAX_AREA = 8_174  # soglia minima dipendente
DETRAZIONE_DIPENDENTE_BASE = 1_955
DETRAZIONE_FIGLIO_MINORENNE = 950  # stima media post-AUU 2025
ADDIZIONALE_REGIONALE_MEDIA = 0.0173
ADDIZIONALE_COMUNALE_MEDIA = 0.005

# =============================================================================
# Mutuo
# =============================================================================
TASSO_MUTUO_ANNUO = 0.042  # ~4,2% medio 2025
DURATA_MUTUO_ANNI = 30


def calcola_irpef(reddito_lordo: float) -> float:
    """IRPEF lorda su scaglioni TUIR 2025."""
    imposta = 0.0
    base = reddito_lordo
    prev = 0.0
    for soglia, aliquota in IRPEF_SCAGLIONI:
        if base <= prev:
            break
        cap = min(base, soglia)
        imposta += (cap - prev) * aliquota
        prev = cap
    return imposta


def detrazione_dipendente(reddito: float) -> float:
    """Detrazione lavoro dipendente, formula TUIR 2025."""
    if reddito <= 15_000:
        return DETRAZIONE_DIPENDENTE_BASE
    if reddito <= 28_000:
        return 1_910 + 1_190 * (28_000 - reddito) / 13_000
    if reddito <= 50_000:
        return 1_910 * (50_000 - reddito) / 22_000
    return 0.0


def reddito_netto_da_lordo(lordo: float, n_figli: int = 0) -> float:
    """Reddito netto annuo: irpef - detrazioni - addizionali."""
    irpef_lorda = calcola_irpef(lordo)
    detr = detrazione_dipendente(lordo) + n_figli * DETRAZIONE_FIGLIO_MINORENNE
    irpef_netta = max(0, irpef_lorda - detr)
    addizionali = lordo * (ADDIZIONALE_REGIONALE_MEDIA + ADDIZIONALE_COMUNALE_MEDIA)
    return lordo - irpef_netta - addizionali


def reddito_lordo_per_netto_target(netto_target: float, n_figli: int = 0,
                                   n_percettori: int = 1) -> float:
    """Inverte la funzione netto→lordo per trovare il lordo familiare necessario.
    Bisezione su [0, 250k]. Se ci sono N percettori il lordo si distribuisce."""
    if netto_target <= 0:
        return 0.0
    netto_per_percettore = netto_target / n_percettori
    figli_per_percettore = n_figli  # detrazioni solo a un percettore (default)

    lo, hi = 0.0, 250_000.0
    for _ in range(40):
        mid = (lo + hi) / 2
        netto_p = reddito_netto_da_lordo(mid, figli_per_percettore if hi > lo else 0)
        # Detrazioni figli vanno solo a uno (semplificazione):
        # qui calcolo puro su un percettore con tutti i figli, poi moltiplico
        if netto_p < netto_per_percettore:
            lo = mid
        else:
            hi = mid
    lordo_per_uno = (lo + hi) / 2
    return lordo_per_uno * n_percettori


def costo_casa_annuo(prof: dict, modalita: str, affitto_mq_mese: float | None,
                     prezzo_acq_mq: float | None) -> float | None:
    """Costo casa annuo per profilo + modalita."""
    mq = prof["mq"]
    if modalita == "affitto":
        if not affitto_mq_mese:
            return None
        return mq * affitto_mq_mese * 12
    if modalita == "mutuo":
        if not prezzo_acq_mq:
            return None
        # Surrogato semplice: prezzo_totale × tasso_annuo (interessi anno 1, ammortamento francese)
        # Approssimazione decente: per durata 30y rata costante ≈ prezzo × 0,059
        prezzo_totale = mq * prezzo_acq_mq
        coeff_rata = 0.059  # rata annua su 30 anni a 4,2% = ~5,9% del capitale
        return prezzo_totale * coeff_rata
    if modalita == "proprieta":
        # Solo IMU + manutenzione + bollette extra-paniere ≈ ipotetici 1500 €/anno
        return 1500.0
    return None


def reddito_sostenibile(prof_key: str, affitto_mq_mese: float | None,
                        prezzo_acq_mq: float | None,
                        modalita: str = "affitto") -> dict:
    """Calcola il reddito lordo familiare necessario per coprire le spese
    nel comune dato. Modalita: affitto / mutuo / proprieta."""
    prof = PROFILI[prof_key]
    paniere = PANIERE_NON_CASA[prof_key]
    casa = costo_casa_annuo(prof, modalita, affitto_mq_mese, prezzo_acq_mq)
    if casa is None:
        return {"profilo": prof_key, "modalita": modalita,
                "casa_annuo_eur": None, "paniere_annuo_eur": paniere,
                "spesa_totale_eur": None, "lordo_richiesto_eur": None}
    spesa_totale = paniere + casa
    lordo = reddito_lordo_per_netto_target(spesa_totale, prof["figli"], prof["percettori"])
    return {
        "profilo": prof_key,
        "profilo_label": prof["label"],
        "modalita": modalita,
        "mq": prof["mq"],
        "casa_annuo_eur": round(casa),
        "paniere_annuo_eur": paniere,
        "spesa_totale_eur": round(spesa_totale),
        "lordo_richiesto_eur": round(lordo),
    }


# =============================================================================
# Indice qualita composito
# =============================================================================
def normalize(series: pd.Series, invert: bool = False) -> pd.Series:
    """Normalizza una serie su 0-100. invert=True per indici dove "minore = meglio"."""
    s = series.astype(float)
    lo, hi = s.quantile(0.05), s.quantile(0.95)
    if hi - lo < 1e-6:
        return pd.Series([50.0] * len(s), index=s.index)
    norm = ((s - lo) / (hi - lo) * 100).clip(0, 100)
    if invert:
        norm = 100 - norm
    return norm


def calcola_indice_qualita(df: pd.DataFrame) -> pd.Series:
    """Indice composito 0-100 (default).

    Pesi default (in assenza di criminalita/servizi F3):
      60% residuo netto annuo (mediana_netta - costo_vita_minimo_single)
      40% accessibilita casa (1 - prezzo_acq_norm)

    Quando F3 arriva, i pesi diventano:
      40% residuo
      20% accessibilita casa
      20% servizi BES
      15% sicurezza (1 - delittuosita_norm)
       5% disuguaglianza (1 - polarizzazione_norm)
    """
    # Reddito netto stimato sulla mediana (single, 0 figli)
    netto_mediana = df["reddito_mediana"].apply(
        lambda x: reddito_netto_da_lordo(x, 0) if pd.notna(x) else None
    )
    # Costo casa minimo: profilo single in affitto
    costo_minimo = (df.get("affitto_eur_mq_mese_med", pd.Series([None] * len(df)))
                    * 50 * 12)
    paniere_single = PANIERE_NON_CASA["single"]
    residuo = netto_mediana - costo_minimo - paniere_single
    df["residuo_netto_annuo"] = residuo

    # Score componenti
    score_residuo = normalize(residuo)
    if df.get("prezzo_acq_eur_mq_med") is not None:
        score_casa_acq = normalize(df["prezzo_acq_eur_mq_med"], invert=True)
    else:
        score_casa_acq = pd.Series([50.0] * len(df), index=df.index)

    score_finale = 0.6 * score_residuo.fillna(0) + 0.4 * score_casa_acq.fillna(50)
    return score_finale.round(1)


# =============================================================================
# Main
# =============================================================================
def main() -> int:
    redditi_path = PROC / "comuni_redditi_ultimo.csv"
    omi_path = PROC / "omi_comuni.csv"

    if not redditi_path.exists():
        log.error("manca %s", redditi_path)
        return 1
    df_red = pd.read_csv(redditi_path, dtype={"codice_istat": str, "sigla_provincia": str},
                         keep_default_na=False, na_values=[""])
    log.info("redditi: %d comuni", len(df_red))

    if omi_path.exists():
        df_omi = pd.read_csv(omi_path, dtype={"codice_istat": str, "sigla_provincia": str},
                             keep_default_na=False, na_values=[""])
        log.info("omi: %d comuni", len(df_omi))
        df = df_red.merge(
            df_omi[["codice_istat", "n_zone", "n_zone_con_dati",
                    "prezzo_acq_eur_mq_med", "affitto_eur_mq_mese_med",
                    "fascia_centrale_disponibile", "semestre"]],
            on="codice_istat", how="left"
        )
    else:
        log.warning("omi_comuni.csv non trovato — proseguo solo con redditi")
        df = df_red.copy()
        df["prezzo_acq_eur_mq_med"] = None
        df["affitto_eur_mq_mese_med"] = None

    # Pre-calcolo reddito sostenibile per ogni profilo, modalita affitto
    log.info("calcolo reddito sostenibile per %d profili x %d comuni", len(PROFILI), len(df))
    for prof_key in PROFILI:
        col = f"rs_{prof_key}_affitto"
        df[col] = df["affitto_eur_mq_mese_med"].apply(
            lambda x, p=prof_key: reddito_sostenibile(p, x, None, "affitto")["lordo_richiesto_eur"]
            if pd.notna(x) else None
        )
        # Residuo: mediana_netta vs reddito sostenibile
        df[f"residuo_{prof_key}_affitto"] = df.apply(
            lambda r, p=prof_key, c=col: (
                round(reddito_netto_da_lordo(r["reddito_mediana"],
                                              PROFILI[p]["figli"]) -
                      reddito_netto_da_lordo(r[c],
                                              PROFILI[p]["figli"]))
                if pd.notna(r["reddito_mediana"]) and pd.notna(r[c]) else None
            ), axis=1
        )

    # Indice qualita composito
    df["indice_qualita"] = calcola_indice_qualita(df)

    # Output
    out_csv = PROC / "comuni_indicatori.csv"
    df.to_csv(out_csv, index=False)
    log.info("scritto %s (%d comuni)", out_csv, len(df))

    # Output JSON: TUTTI i comuni (anche senza OMI). Flag omi_disponibile
    # permette al frontend di gestire gracefully i casi senza prezzi/affitti.
    df_dash = df.copy()
    df_dash["omi_disponibile"] = df_dash["affitto_eur_mq_mese_med"].notna()
    cols_essenziali = [
        "codice_istat", "comune", "sigla_provincia", "regione",
        "n_contribuenti", "reddito_mediana", "reddito_p10", "reddito_p90",
        "ratio_p90_p10", "pct_sotto_15k", "pct_sopra_55k",
        "prezzo_acq_eur_mq_med", "affitto_eur_mq_mese_med",
        "rs_single_affitto", "rs_coppia_affitto", "rs_coppia_2f_affitto",
        "residuo_single_affitto", "residuo_coppia_2f_affitto",
        "indice_qualita", "omi_disponibile",
    ]
    cols_present = [c for c in cols_essenziali if c in df_dash.columns]
    payload = {
        "meta": {
            "n_comuni": len(df_dash),
            "n_comuni_totali": len(df),
            "anno_redditi": int(df["anno"].iloc[0]) if "anno" in df.columns else None,
            "semestre_omi": str(df["semestre"].iloc[0]) if "semestre" in df.columns and len(df) else None,
            "profili": {k: v for k, v in PROFILI.items()},
            "paniere_non_casa": PANIERE_NON_CASA,
        },
        "comuni": df_dash[cols_present].astype(object).where(
            pd.notna(df_dash[cols_present]), None).to_dict(orient="records"),
    }
    out_json = OUT_DATA / "comuni.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    log.info("scritto %s (%.1f KB, %d comuni)",
             out_json, out_json.stat().st_size / 1024, len(df_dash))

    # Sanity print
    big = df[(df["n_contribuenti"] > 50_000) & df["affitto_eur_mq_mese_med"].notna()].copy()
    if len(big):
        print("\n=== Top 10 comuni grandi per indice qualita ===")
        cols = ["comune", "sigla_provincia", "n_contribuenti", "reddito_mediana",
                "affitto_eur_mq_mese_med", "rs_single_affitto", "residuo_single_affitto",
                "indice_qualita"]
        cols = [c for c in cols if c in big.columns]
        print(big.nlargest(10, "indice_qualita")[cols].to_string(index=False))
        print("\n=== Bottom 10 comuni grandi per indice qualita ===")
        print(big.nsmallest(10, "indice_qualita")[cols].to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
