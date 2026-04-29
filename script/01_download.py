#!/usr/bin/env python3
"""
Download dei dati base per la dashboard qualita-vita-italia (F1).

In F1 scarichiamo:
- MEF Dipartimento delle Finanze: dichiarazioni IRPEF su base comunale,
  un file zip per anno (~7.900 comuni × 8 fasce di reddito complessivo).
- GeoJSON comuni ISTAT (via OpenPolis mirror).

Le sorgenti aggiuntive (OMI bulk, ISTAT IPC, delittuosita, BES) vengono
scaricate dai rispettivi script ETL nelle fasi successive (F2, F3, F4)
per non monolitizzare questo step.

Idempotente: salta i file gia scaricati con la stessa lunghezza.
Sonda anni successivi all'ultimo MEF noto per intercettare nuove pubblicazioni.
"""

from __future__ import annotations
import json
import logging
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("download")

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "dati" / "raw"
RAW_MEF = RAW / "mef"
RAW_GEO = RAW / "geo"
RAW_MEF.mkdir(parents=True, exist_ok=True)
RAW_GEO.mkdir(parents=True, exist_ok=True)

MANIFEST_PATH = RAW / "manifest.json"

MEF_URL_TPL = (
    "https://www1.finanze.gov.it/finanze/analisi_stat/public/"
    "v_4_0_0/contenuti/Redditi_e_principali_variabili_IRPEF_su_base_comunale_CSV_{year}.zip"
)
MEF_FIRST_YEAR = 2017
MEF_LATEST_GUESS = date.today().year - 1

GEOJSON_COMUNI = (
    "https://raw.githubusercontent.com/openpolis/geojson-italy/master/"
    "geojson/limits_IT_municipalities.geojson"
)
GEOJSON_REGIONI = (
    "https://raw.githubusercontent.com/openpolis/geojson-italy/master/"
    "geojson/limits_IT_regions.geojson"
)


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {"mef": {}, "geo": {}}


def save_manifest(m: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(m, indent=2, ensure_ascii=False))


def http_head(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {})
    except Exception as e:
        log.warning("HEAD %s failed: %s", url, e)
        return 0, {}


def http_download(url: str, dest: Path) -> bool:
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)
        tmp.rename(dest)
        log.info("downloaded %s -> %s (%d bytes)", url, dest.name, dest.stat().st_size)
        return True
    except Exception as e:
        log.error("download failed %s: %s", url, e)
        if tmp.exists():
            tmp.unlink()
        return False


def sync_mef(manifest: dict) -> list[int]:
    available_years: list[int] = []
    last_known = max((int(y) for y in manifest["mef"]), default=MEF_FIRST_YEAR - 1)
    probe_to = max(last_known + 2, MEF_LATEST_GUESS + 1)

    for year in range(MEF_FIRST_YEAR, probe_to + 1):
        url = MEF_URL_TPL.format(year=year)
        dest = RAW_MEF / f"irpef_{year}.zip"
        meta = manifest["mef"].get(str(year), {})

        if dest.exists() and meta.get("size") == dest.stat().st_size:
            available_years.append(year)
            continue

        status, headers = http_head(url)
        if status != 200:
            if year > last_known:
                log.info("anno %d non ancora pubblicato (HTTP %d)", year, status)
            else:
                log.warning("anno %d: HEAD %d", year, status)
            continue

        size = int(headers.get("Content-Length", 0))
        last_modified = headers.get("Last-Modified", "")

        if dest.exists() and size and dest.stat().st_size == size:
            manifest["mef"][str(year)] = {"size": size, "last_modified": last_modified, "url": url}
            available_years.append(year)
            continue

        if http_download(url, dest):
            manifest["mef"][str(year)] = {
                "size": dest.stat().st_size,
                "last_modified": last_modified,
                "url": url,
                "downloaded_at": date.today().isoformat(),
            }
            available_years.append(year)

    return sorted(available_years)


def sync_geo(manifest: dict) -> None:
    geojson_targets = [
        ("comuni", GEOJSON_COMUNI, RAW_GEO / "comuni.geojson"),
        ("regioni", GEOJSON_REGIONI, RAW_GEO / "regioni.geojson"),
    ]
    for key, url, dest in geojson_targets:
        if dest.exists() and manifest.get("geo", {}).get(key, {}).get("size") == dest.stat().st_size:
            continue
        if http_download(url, dest):
            manifest.setdefault("geo", {})[key] = {
                "url": url,
                "size": dest.stat().st_size,
                "downloaded_at": date.today().isoformat(),
            }


def main() -> int:
    manifest = load_manifest()

    log.info("=== MEF ===")
    years = sync_mef(manifest)
    log.info("anni MEF disponibili: %s", years)

    log.info("=== GeoJSON ===")
    sync_geo(manifest)

    save_manifest(manifest)
    log.info("manifest scritto in %s", MANIFEST_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
