#!/usr/bin/env bash
# Wrapper the scheduler invokes. Handles:
#   - CWD setup
#   - venv activation
#   - ET-window gating (runs only if current ET time is within the slot window,
#     so DST drift and slightly-off-schedule fires don't trigger spurious runs)
#   - Dedup: skip if a row for this slot+date already exists in history.
#
# Usage: run_slot.sh open|close|brief
set -euo pipefail

SLOT="${1:?usage: run_slot.sh open|close|brief}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d)-$SLOT.log"

# Runs log: committed via history/ dir. Lets us see every trigger fire in the repo.
RUNS_LOG="$PROJECT_DIR/history/runs.log"
log_run() { echo "$(date -Iseconds) slot=$SLOT ET=$(TZ=America/New_York date +'%Y-%m-%d %H:%M') $*" >> "$RUNS_LOG"; }

exec >>"$LOG_FILE" 2>&1
echo "---- $(date -Iseconds) run_slot.sh $SLOT ----"
log_run "START"

# ET gating
ET_HOUR=$(TZ=America/New_York date +%H)
ET_MIN=$(TZ=America/New_York date +%M)
ET_DOW=$(TZ=America/New_York date +%u)  # 1-7, Mon-Sun

# Skip weekends
if [ "$ET_DOW" -ge 6 ]; then
  echo "weekend in ET; skipping"
  log_run "SKIP weekend"
  exit 0
fi

# Window checks (±15min around target).
# open:  10:00 ET → window 09:45–10:15
# close: 15:00 ET → window 14:45–15:15
# brief: 15:05 ET → window 15:00–15:30 (runs AFTER close has landed)
ok_open()  { [ "$ET_HOUR" -eq 9 ]  && [ "$ET_MIN" -ge 45 ] || [ "$ET_HOUR" -eq 10 ] && [ "$ET_MIN" -le 15 ]; }
ok_close() { [ "$ET_HOUR" -eq 14 ] && [ "$ET_MIN" -ge 45 ] || [ "$ET_HOUR" -eq 15 ] && [ "$ET_MIN" -le 15 ]; }
ok_brief() { [ "$ET_HOUR" -eq 15 ] && [ "$ET_MIN" -ge 0 ]  && [ "$ET_MIN" -le 30 ]; }

case "$SLOT" in
  open)  ok_open  || { echo "not in open window (ET=$ET_HOUR:$ET_MIN); skipping"; log_run "SKIP out-of-window"; exit 0; } ;;
  close) ok_close || { echo "not in close window (ET=$ET_HOUR:$ET_MIN); skipping"; log_run "SKIP out-of-window"; exit 0; } ;;
  brief) ok_brief || { echo "not in brief window (ET=$ET_HOUR:$ET_MIN); skipping"; log_run "SKIP out-of-window"; exit 0; } ;;
  *) echo "unknown slot: $SLOT"; log_run "ERR unknown-slot"; exit 2 ;;
esac

# Dedup: skip if today's month JSONL already has a row for this slot + today's ET date
ET_DATE=$(TZ=America/New_York date +%Y-%m-%d)
MONTH_FILE="$PROJECT_DIR/history/$(TZ=America/New_York date +%Y-%m).jsonl"
if [ -f "$MONTH_FILE" ]; then
  if grep -q "\"slot\": \"$SLOT\"" "$MONTH_FILE" && \
     grep "\"slot\": \"$SLOT\"" "$MONTH_FILE" | grep -q "$ET_DATE"; then
    echo "already have a $SLOT row for $ET_DATE; skipping"
    log_run "SKIP dedup"
    exit 0
  fi
fi

# First-run venv setup (remote triggers get a fresh checkout each run)
if [ ! -d "$PROJECT_DIR/.venv" ]; then
  echo "creating venv..."
  python3 -m venv "$PROJECT_DIR/.venv"
  "$PROJECT_DIR/.venv/bin/pip" install -q --upgrade pip
  "$PROJECT_DIR/.venv/bin/pip" install -q -r "$PROJECT_DIR/requirements.txt"
fi

# Run
log_run "RUN agent"
"$PROJECT_DIR/.venv/bin/python" run_agent.py --slot "$SLOT"
log_run "DONE rc=$?"
