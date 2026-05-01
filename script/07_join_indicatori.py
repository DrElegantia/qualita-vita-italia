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
# =============================================================================
PANIERE_NON_CASA = {
    "single":    8_400,    # alimentari + trasporti + utenze + servizi + tempo libero
    "coppia":   14_500,
    "coppia2":  14_500,
    "coppia_1f": 17_400,
    "coppia_2f": 21_000,
}

# IPC regionale (NIC base 2015, anno 2024) hardcoded come moltiplicatore del paniere.
# Fonte: ISTAT serie storica IPC NIC. Valori medi degli ultimi 12 mesi mobili,
# normalizzati a 1.00 = media nazionale.
# Quando l'endpoint sdmx.istat.it tornera up sostituiremo con fetch dinamico.
IPC_REGIONALE = {
    "Trentino-Alto Adige": 1.10,
    "Lombardia":           1.05,
    "Emilia-Romagna":      1.04,
    "Veneto":              1.03,
    "Liguria":             1.02,
    "Friuli-Venezia Giulia": 1.01,
    "Piemonte":            1.00,
    "Toscana":             1.00,
    "Lazio":               1.00,
    "Valle d'Aosta":       1.00,
    "Marche":              0.97,
    "Umbria":              0.97,
    "Sardegna":            0.95,
    "Abruzzo":             0.94,
    "Campania":            0.93,
    "Puglia":              0.93,
    "Molise":              0.92,
    "Basilicata":          0.91,
    "Sicilia":             0.91,
    "Calabria":            0.90,
}


def paniere_regionale(prof_key: str, regione: str | None) -> float:
    """Paniere non-casa applicato all'IPC regionale (default 1.0)."""
    base = PANIERE_NON_CASA[prof_key]
    mult = IPC_REGIONALE.get(regione or "", 1.0)
    return base * mult

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
                        modalita: str = "affitto",
                        regione: str | None = None) -> dict:
    """Calcola il reddito lordo familiare necessario per coprire le spese
    nel comune dato. Modalita: affitto / mutuo / proprieta.
    Paniere modulato per IPC regionale (default 1.0 se regione sconosciuta)."""
    prof = PROFILI[prof_key]
    paniere = paniere_regionale(prof_key, regione)
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
    # Paniere modulato per IPC regionale (single)
    paniere_per_riga = df["regione"].apply(
        lambda r: paniere_regionale("single", r) if pd.notna(r) else PANIERE_NON_CASA["single"]
    )
    residuo = netto_mediana - costo_minimo - paniere_per_riga
    df["residuo_netto_annuo"] = residuo

    # Score componenti
    score_residuo = normalize(residuo)
    if df.get("prezzo_acq_eur_mq_med") is not None:
        score_casa_acq = normalize(df["prezzo_acq_eur_mq_med"], invert=True)
    else:
        score_casa_acq = pd.Series([50.0] * len(df), index=df.index)

    # Disuguaglianza P90/P10: alto = peggio (penalita)
    if "ratio_p90_p10" in df.columns:
        score_disug = normalize(df["ratio_p90_p10"], invert=True)
    else:
        score_disug = pd.Series([50.0] * len(df), index=df.index)

    # Servizi BES (provinciale ISTAT, 11 indicatori)
    score_bes = df["score_bes"].fillna(50) if "score_bes" in df.columns and df["score_bes"].notna().any() \
                else pd.Series([50.0] * len(df), index=df.index)

    # S24H per macro-area (provinciale, 24 indicatori)
    def get_s24h(col):
        return df[col].fillna(50) if col in df.columns and df[col].notna().any() \
               else pd.Series([50.0] * len(df), index=df.index)
    s24h_sanita    = get_s24h("score_s24h_sanita")
    s24h_sicurezza = get_s24h("score_s24h_sicurezza")
    s24h_ambiente  = get_s24h("score_s24h_ambiente")
    s24h_vita      = get_s24h("score_s24h_vita")

    # Pesi composito v3 (con S24H integrato):
    # 28% residuo netto      (capacita di spesa dopo casa)
    # 15% accessibilita casa (1 - prezzo acquisto OMI)
    # 12% BES ISTAT          (salute, istruzione, lavoro provinciale)
    # 12% S24H sicurezza     (4 sotto-indicatori, piu' ricco di tasso delitti raw)
    # 10% S24H sanita        (mortalita evitabile/tumore, emigrazione, medici)
    # 10% S24H ambiente      (aria, clima, ecosistema, rischio idrogeologico)
    #  8% S24H vita          (cultura, ristoranti, librerie, palestre)
    #  5% disuguaglianza     (P90/P10)
    score_finale = (0.28 * score_residuo.fillna(0)
                    + 0.15 * score_casa_acq.fillna(50)
                    + 0.12 * score_bes
                    + 0.12 * s24h_sicurezza
                    + 0.10 * s24h_sanita
                    + 0.10 * s24h_ambiente
                    + 0.08 * s24h_vita
                    + 0.05 * score_disug.fillna(50))
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

    # Join ISTAT delitti capoluoghi: per ogni provincia, prendo il dato del capoluogo
    # e lo applico a tutti i comuni della provincia (proxy provinciale).
    delitti_path = PROC / "istat_delitti_capoluoghi.csv"
    if delitti_path.exists():
        df_delitti = pd.read_csv(delitti_path, dtype={"codice_istat": str},
                                  keep_default_na=False, na_values=[""])
        # Per ogni capoluogo abbiamo il codice ISTAT comunale → mappo a provincia via df
        cap_to_prov = df.set_index("codice_istat")["sigla_provincia"].to_dict()
        df_delitti["sigla_provincia"] = df_delitti["codice_istat"].map(cap_to_prov)
        # Per ogni provincia: tasso delitti per 10k abitanti del capoluogo (uso n_contribuenti × 1.36 come proxy popolazione).
        cap_pop = df.set_index("codice_istat")["n_contribuenti"].to_dict()
        df_delitti["pop_stimata"] = df_delitti["codice_istat"].map(cap_pop) * 1.36
        df_delitti["tasso_delitti_per_10k"] = (df_delitti["totale_delitti"] /
                                                df_delitti["pop_stimata"] * 10_000).round(1)
        prov_to_tasso = df_delitti.dropna(subset=["sigla_provincia", "tasso_delitti_per_10k"]) \
            .groupby("sigla_provincia")["tasso_delitti_per_10k"].mean().to_dict()
        df["tasso_delitti_per_10k"] = df["sigla_provincia"].map(prov_to_tasso)
        log.info("delitti: join su %d province (covered %d comuni / %d totali)",
                 len(prov_to_tasso),
                 int(df["tasso_delitti_per_10k"].notna().sum()), len(df))
    else:
        log.warning("istat_delitti_capoluoghi.csv non trovato — score sicurezza non calcolato")
        df["tasso_delitti_per_10k"] = None

    # Join ISTAT BES provinciale (NUTS-3, granularita provincia)
    bes_path = PROC / "istat_bes_provinciale.csv"
    if bes_path.exists():
        df_bes = pd.read_csv(bes_path, keep_default_na=False, na_values=[""],
                             dtype={"sigla_provincia": str})
        bes_cols = [c for c in df_bes.columns if c != "sigla_provincia"]
        df = df.merge(df_bes, on="sigla_provincia", how="left")
        log.info("BES: join su %d province × %d indicatori",
                 df_bes.shape[0], len(bes_cols))

        # Score BES sintetico: media z-score per ciascun indicatore (con verso)
        BES_VERSO = {
            "salute_speranza_vita": "+",
            "istruzione_secondaria": "+",
            "istruzione_terziaria": "+",
            "istruzione_neet": "-",
            "istruzione_numeracy_bassa": "-",
            "istruzione_literacy_bassa": "-",
            "lavoro_giovani_occupazione": "+",
            "lavoro_giovani_non_partec": "-",
            "politica_affluenza_regionale": "+",
            "politica_consigliere_donne": "+",
            "servizi_banda_larga": "+",
        }
        zscores = []
        for col, verso in BES_VERSO.items():
            if col not in df.columns:
                continue
            s = df[col].astype(float)
            mu = s.mean()
            sd = s.std()
            if sd > 0:
                z = (s - mu) / sd
                if verso == "-":
                    z = -z
                zscores.append(z)
        if zscores:
            score_bes_raw = sum(zscores) / len(zscores)
            # Normalizza 0-100 (5°-95° percentile)
            df["score_bes"] = normalize(score_bes_raw).round(1)
        else:
            df["score_bes"] = None
    else:
        log.warning("istat_bes_provinciale.csv non trovato — score BES non calcolato")
        df["score_bes"] = None

    # Join Sole24Ore QDV2025 (107 province): 24 indicatori complementari
    # Sanita, sicurezza disaggregata, ambiente/clima, lavoro, casa, cultura, demografia.
    # Licenza CC-BY-NC-4.0: attribuzione obbligatoria (vedi nota metodologica).
    s24h_path = PROC / "sole24h_qdv2025_provincia.csv"
    if s24h_path.exists():
        df_s24h = pd.read_csv(s24h_path, keep_default_na=False, na_values=[""])
        # Mapping DENOMINAZIONE CORRENTE → sigla provincia MEF
        # Heuristic: prendo il comune piu' popoloso per provincia con stesso nome
        # canonico (eccezioni gestite manualmente)
        S24H_NAME_TO_SIGLA = {
            "Torino":"TO","Vercelli":"VC","Biella":"BI","Verbano-Cusio-Ossola":"VB","Novara":"NO",
            "Cuneo":"CN","Asti":"AT","Alessandria":"AL","Aosta":"AO","Imperia":"IM","Savona":"SV",
            "Genova":"GE","La Spezia":"SP","Varese":"VA","Como":"CO","Lecco":"LC","Sondrio":"SO",
            "Milano":"MI","Bergamo":"BG","Brescia":"BS","Pavia":"PV","Lodi":"LO","Cremona":"CR",
            "Mantova":"MN","Monza e Brianza":"MB","Monza e della Brianza":"MB","Bolzano":"BZ",
            "Trento":"TN","Verona":"VR","Vicenza":"VI","Belluno":"BL","Treviso":"TV","Venezia":"VE",
            "Padova":"PD","Rovigo":"RO","Pordenone":"PN","Udine":"UD","Gorizia":"GO","Trieste":"TS",
            "Piacenza":"PC","Parma":"PR","Reggio Emilia":"RE","Reggio nell'Emilia":"RE","Modena":"MO",
            "Bologna":"BO","Ferrara":"FE","Ravenna":"RA","Forlì-Cesena":"FC","Rimini":"RN",
            "Massa Carrara":"MS","Massa-Carrara":"MS","Lucca":"LU","Pistoia":"PT","Firenze":"FI",
            "Prato":"PO","Livorno":"LI","Pisa":"PI","Arezzo":"AR","Siena":"SI","Grosseto":"GR",
            "Perugia":"PG","Terni":"TR","Pesaro e Urbino":"PU","Ancona":"AN","Macerata":"MC",
            "Ascoli Piceno":"AP","Fermo":"FM","Viterbo":"VT","Rieti":"RI","Roma":"RM","Latina":"LT",
            "Frosinone":"FR","L'Aquila":"AQ","Teramo":"TE","Pescara":"PE","Chieti":"CH","Isernia":"IS",
            "Campobasso":"CB","Caserta":"CE","Benevento":"BN","Napoli":"NA","Avellino":"AV",
            "Salerno":"SA","Foggia":"FG","Bari":"BA","Barletta-Andria-Trani":"BT","Taranto":"TA",
            "Brindisi":"BR","Lecce":"LE","Potenza":"PZ","Matera":"MT","Cosenza":"CS","Crotone":"KR",
            "Catanzaro":"CZ","Vibo Valentia":"VV","Reggio Calabria":"RC","Reggio di Calabria":"RC",
            "Trapani":"TP","Palermo":"PA","Messina":"ME","Agrigento":"AG","Caltanissetta":"CL",
            "Enna":"EN","Catania":"CT","Ragusa":"RG","Siracusa":"SR","Sassari":"SS","Nuoro":"NU",
            "Cagliari":"CA","Oristano":"OR","Sud Sardegna":"SU",
        }
        df_s24h["sigla_provincia"] = df_s24h["provincia_s24h"].map(S24H_NAME_TO_SIGLA)
        unmapped = df_s24h[df_s24h["sigla_provincia"].isna()]["provincia_s24h"].tolist()
        if unmapped:
            log.warning("S24H non mappate (%d): %s", len(unmapped), unmapped[:5])
        df_s24h = df_s24h.dropna(subset=["sigla_provincia"])
        s24h_cols = [c for c in df_s24h.columns if c not in ("provincia_s24h", "sigla_provincia")]
        df = df.merge(df_s24h[["sigla_provincia"] + s24h_cols], on="sigla_provincia", how="left")
        log.info("S24H: join su %d province × %d indicatori", len(df_s24h), len(s24h_cols))

        # Score S24H per macro-area (z-score con verso, normalizzato 5-95)
        S24H_GROUPS = {
            "score_s24h_sanita": [
                ("sanita_mortalita_evitabile", "-"),
                ("sanita_mortalita_tumore", "-"),
                ("sanita_emigrazione_osp", "-"),
                ("sanita_medici_mmg", "+"),
            ],
            "score_s24h_sicurezza": [
                ("sic_indice_crim", "-"),
                ("sic_percezione_insic", "-"),
                ("sic_mortalita_stradale", "-"),
            ],
            "score_s24h_ambiente": [
                ("amb_aria", "+"),
                ("amb_clima", "+"),
                ("amb_ecosistema", "+"),
                ("amb_diff_rifiuti", "+"),
                ("amb_aree_protette", "+"),
                ("amb_rischio_alluvione", "-"),
                ("amb_rischio_frana", "-"),
            ],
            "score_s24h_lavoro": [
                ("lav_disocc_giovani", "-"),
                ("lav_non_partec", "-"),
                ("lav_retribuzione_media", "+"),
            ],
            "score_s24h_vita": [
                ("vita_ristoranti", "+"),
                ("vita_librerie", "+"),
                ("vita_palestre", "+"),
                ("vita_offerta_cult", "+"),
            ],
        }
        for col_out, indicators in S24H_GROUPS.items():
            zscores = []
            for col, verso in indicators:
                if col not in df.columns:
                    continue
                s = df[col].astype(float)
                mu, sd = s.mean(), s.std()
                if sd > 0:
                    z = (s - mu) / sd
                    if verso == "-":
                        z = -z
                    zscores.append(z)
            if zscores:
                df[col_out] = normalize(sum(zscores) / len(zscores)).round(1)
            else:
                df[col_out] = None
    else:
        log.warning("sole24h_qdv2025_provincia.csv non trovato — score S24H non calcolato")
        for k in ("score_s24h_sanita", "score_s24h_sicurezza", "score_s24h_ambiente",
                  "score_s24h_lavoro", "score_s24h_vita"):
            df[k] = None

    # Pre-calcolo reddito sostenibile per ogni profilo, modalita affitto
    log.info("calcolo reddito sostenibile per %d profili x %d comuni", len(PROFILI), len(df))
    for prof_key in PROFILI:
        col = f"rs_{prof_key}_affitto"
        df[col] = df.apply(
            lambda r, p=prof_key: (
                reddito_sostenibile(p, r["affitto_eur_mq_mese_med"], None, "affitto",
                                    r.get("regione"))["lordo_richiesto_eur"]
                if pd.notna(r["affitto_eur_mq_mese_med"]) else None
            ), axis=1
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
        "score_bes", "tasso_delitti_per_10k",
        "score_s24h_sanita", "score_s24h_sicurezza",
        "score_s24h_ambiente", "score_s24h_vita",
    ]
    cols_present = [c for c in cols_essenziali if c in df_dash.columns]

    # KPI mediana nazionale (pesata su contribuenti)
    valid = df_dash.dropna(subset=["reddito_mediana", "n_contribuenti"])
    mediana_naz = (int(round((valid["reddito_mediana"] * valid["n_contribuenti"]).sum()
                              / valid["n_contribuenti"].sum()))
                   if len(valid) else None)

    # Semestre OMI: convert "20252" (int/str) → "Sem. 2 2025" leggibile
    sem_raw = None
    if "semestre" in df.columns and len(df):
        sem_raw_val = df["semestre"].dropna().iloc[0] if df["semestre"].dropna().size else None
        if sem_raw_val is not None:
            try:
                s = str(int(float(sem_raw_val)))  # 20252 normalized
                if len(s) == 5:
                    sem_raw = f"Sem. {s[4]} {s[:4]}"
                else:
                    sem_raw = s
            except (ValueError, TypeError):
                sem_raw = str(sem_raw_val)

    payload = {
        "meta": {
            "n_comuni": len(df_dash),
            "n_comuni_totali": len(df),
            "n_comuni_con_omi": int(df_dash["omi_disponibile"].sum()),
            "anno_redditi": int(df["anno"].iloc[0]) if "anno" in df.columns else None,
            "semestre_omi": sem_raw,
            "mediana_naz": mediana_naz,
            "profili": {k: v for k, v in PROFILI.items()},
            "paniere_non_casa": PANIERE_NON_CASA,
            "ipc_regionale": IPC_REGIONALE,
            "indice_pesi": {
                "residuo": 0.28, "casa": 0.15, "bes": 0.12,
                "s24h_sicurezza": 0.12, "s24h_sanita": 0.10,
                "s24h_ambiente": 0.10, "s24h_vita": 0.08,
                "disuguaglianza": 0.05,
            },
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
