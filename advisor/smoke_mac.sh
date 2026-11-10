#!/bin/bash
# CI smoke test: run the built app against a real app launch, in test mode (no dialogs, no autostart).
set -uo pipefail
export AIMA_TEST=1
export AIMA_DATA="${RUNNER_TEMP:-/tmp}/aima-smoke"
rm -rf "$AIMA_DATA"
BIN=dist/AIMemoryAdvisor-mac/AIMemoryAdvisor.app/Contents/MacOS/AIMemoryAdvisor
"$BIN" >/dev/null 2>&1 &
sleep 10
(open -a TextEdit >/dev/null 2>&1 &)
if [ -d "/Applications/Google Chrome.app" ]; then (open -g -a "Google Chrome" --args --no-first-run --no-default-browser-check >/dev/null 2>&1 &); fi
sleep 60
echo "--- event types"; cat "$AIMA_DATA"/logs/*.jsonl | sed -E 's/.*"type": "([a-z_]+)".*/\1/' | sort | uniq -c
echo "--- app starts"; grep '"app_start"' "$AIMA_DATA"/logs/*.jsonl || true
echo "--- file sets"; ls -la "$AIMA_DATA/filesets" 2>/dev/null || echo "none"
echo "--- error log"; cat "$AIMA_DATA/error.log" 2>/dev/null || echo "none"
"$BIN" --once; "$BIN" --share; "$BIN" --stop
sleep 2
fail=0
grep -q '"session_start"' "$AIMA_DATA"/logs/*.jsonl || { echo "FAIL: no session_start"; fail=1; }
grep -q '"app_start", "app": "textedit"' "$AIMA_DATA"/logs/*.jsonl || { echo "FAIL: TextEdit launch not seen"; fail=1; }
[ -f "$AIMA_DATA/status.html" ] || { echo "FAIL: no report"; fail=1; }
ls "$AIMA_DATA"/share/*.zip >/dev/null 2>&1 || { echo "FAIL: no share zip"; fail=1; }
[ ! -f "$AIMA_DATA/advisor.lock" ] || { echo "FAIL: lock left after stop"; fail=1; }
[ ! -s "$AIMA_DATA/error.log" ] || { echo "FAIL: errors logged"; fail=1; }
pkill -x TextEdit || true; pkill -x "Google Chrome" || true
exit $fail