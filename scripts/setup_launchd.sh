#!/usr/bin/env bash
# Generate & load launchd plists for the two daily slots on macOS.
#
# Strategy: launchd has no per-job timezone, so we fire at SEVERAL UK wall
# times (to cover GMT vs BST vs US DST edge weeks). run_slot.sh gates on
# actual ET time + deduplicates, so redundant fires are no-ops.
#
# Fires at every 5min in two UK-wall-time bands:
#   open  band: 14:40–15:20 UK  (covers 09:40–10:20 ET across all DST states)
#   close band: 19:40–20:20 UK  (covers 14:40–15:20 ET across all DST states)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_DIR"

make_plist() {
  local slot="$1"; shift
  local label="com.trading-claude.$slot"
  local plist="$LAUNCH_DIR/$label.plist"

  # Build <dict><key>Hour</key>...<key>Minute</key>...</dict> entries
  local calendar_entries=""
  for hhmm in "$@"; do
    local hh="${hhmm%%:*}"
    local mm="${hhmm##*:}"
    calendar_entries+="
    <dict>
      <key>Hour</key><integer>$((10#$hh))</integer>
      <key>Minute</key><integer>$((10#$mm))</integer>
      <key>Weekday</key><integer>1</integer>
    </dict>
    <dict>
      <key>Hour</key><integer>$((10#$hh))</integer>
      <key>Minute</key><integer>$((10#$mm))</integer>
      <key>Weekday</key><integer>2</integer>
    </dict>
    <dict>
      <key>Hour</key><integer>$((10#$hh))</integer>
      <key>Minute</key><integer>$((10#$mm))</integer>
      <key>Weekday</key><integer>3</integer>
    </dict>
    <dict>
      <key>Hour</key><integer>$((10#$hh))</integer>
      <key>Minute</key><integer>$((10#$mm))</integer>
      <key>Weekday</key><integer>4</integer>
    </dict>
    <dict>
      <key>Hour</key><integer>$((10#$hh))</integer>
      <key>Minute</key><integer>$((10#$mm))</integer>
      <key>Weekday</key><integer>5</integer>
    </dict>"
  done

  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJECT_DIR/scripts/run_slot.sh</string>
    <string>$slot</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>StandardOutPath</key><string>$PROJECT_DIR/logs/launchd-$slot.out.log</string>
  <key>StandardErrorPath</key><string>$PROJECT_DIR/logs/launchd-$slot.err.log</string>
  <key>StartCalendarInterval</key>
  <array>$calendar_entries
  </array>
</dict>
</plist>
PLIST

  echo "wrote $plist"
  launchctl unload "$plist" 2>/dev/null || true
  launchctl load "$plist"
  echo "loaded $label"
}

mkdir -p "$PROJECT_DIR/logs"

# UK wall times: every 5 min in a 40-min band around each target
make_plist open  14:40 14:45 14:50 14:55 15:00 15:05 15:10 15:15 15:20
make_plist close 19:40 19:45 19:50 19:55 20:00 20:05 20:10 20:15 20:20

echo ""
echo "Done. To remove later:"
echo "  launchctl unload ~/Library/LaunchAgents/com.trading-claude.open.plist"
echo "  launchctl unload ~/Library/LaunchAgents/com.trading-claude.close.plist"
