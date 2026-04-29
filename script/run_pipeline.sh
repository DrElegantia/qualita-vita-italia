#!/usr/bin/env bash
# Pipeline qualita-vita-italia: download + ETL completo + build dashboard.
# Idempotente. Notifica macOS quando un dataset si aggiorna.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/automation"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/pipeline.log"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

notify() {
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"$1\" with title \"qualita-vita-italia\""
  fi
}

log "=== qualita-vita-italia: pipeline start ==="

# Snapshot anni MEF prima del download
years_before=$(ls dati/raw/mef/irpef_*.zip 2>/dev/null | wc -l | tr -d ' ')

log "Step 1: download (MEF + GeoJSON)"
python3 script/01_download.py 2>&1 | tee -a "$LOG_FILE"

# Snapshot anni MEF dopo
years_after=$(ls dati/raw/mef/irpef_*.zip 2>/dev/null | wc -l | tr -d ' ')
if [ "$years_after" -gt "$years_before" ]; then
  delta=$((years_after - years_before))
  notify "Nuovi dati MEF: +$delta anno/i"
  log "*** Nuovo anno MEF intercettato (+$delta) ***"
  echo "$(date -Iseconds): nuovo anno MEF (+$delta)" >> "$LOG_DIR/new_data.log"
fi

log "Step 2: ETL redditi (quartili+decili per comune)"
python3 script/02_etl_redditi.py 2>&1 | tee -a "$LOG_FILE"

# Step 3-7 (OMI, ISTAT IPC, criminalita, servizi, indice qualita) saranno aggiunti
# nelle fasi F2-F4 della roadmap. La pipeline corrente copre solo F1.

log "=== pipeline OK ==="
