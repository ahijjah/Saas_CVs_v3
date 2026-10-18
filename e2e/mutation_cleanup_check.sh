#!/usr/bin/env bash
# Proves the E2E teardown. Two runs of one journey:
#   1. E2E_INJECT_FAILURE=after-start: the stack fails after every service is up. Expected: the run fails AND no backend, frontend or
#      PostgreSQL process (or process group member) survives.
#   2. E2E_CLEANUP_MUTATION=no-kill: teardown is mutated to skip stopping processes. Expected: the run fails and the check reports the
#      survivors. The survivors are then killed by this script, so nothing is left behind either way.
# Usage, from the repository root: ./e2e/mutation_cleanup_check.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
JOURNEY="e2e/test_journeys.py::test_viewer_is_refused_and_sees_no_editor"
RECORD="$(mktemp -t e2e_pids.XXXXXX)"
trap 'rm -f "$RECORD"' EXIT

survivors() {
  # Prints every recorded process or group that is still running. A zombie (exited, not yet reaped) is not running. A group counts
  # too: kill -0 on -PID tests the group, so a child left in the backend's or frontend's group is caught.
  while read -r name pid; do
    [ -z "${pid:-}" ] && continue
    state="$(ps -o stat= -p "$pid" 2>/dev/null | tr -d ' ')"
    case "$state" in
      Z*) continue ;;
    esac
    if [ -n "$state" ] || kill -0 -- "-$pid" 2>/dev/null; then
      echo "$name $pid"
    fi
  done < "$RECORD"
}

kill_survivors() {
  while read -r name pid; do
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  done < <(survivors)
}

echo "== run 1: injected failure after start (expect: run fails, no survivors)"
: > "$RECORD"
E2E_PID_RECORD="$RECORD" E2E_INJECT_FAILURE=after-start python3 -m pytest "$JOURNEY" -q -p no:cacheprovider >/dev/null 2>&1
status1=$?
echo "pytest exit: $status1 (non-zero expected); recorded processes: $(wc -l < "$RECORD")"
left1="$(survivors)"
if [ "$status1" -eq 0 ] || [ -n "$left1" ] || [ ! -s "$RECORD" ]; then
  echo "FAIL run 1: exit=$status1 survivors=[${left1}] recorded=$(wc -l < "$RECORD")"
  kill_survivors
  exit 1
fi
echo "PASS run 1: teardown stopped every recorded process after the injected failure"

echo "== run 2: teardown mutated to skip stopping processes (expect: run fails and survivors are detected)"
: > "$RECORD"
E2E_PID_RECORD="$RECORD" E2E_CLEANUP_MUTATION=no-kill python3 -m pytest "$JOURNEY" -q -p no:cacheprovider >/dev/null 2>&1
status2=$?
left2="$(survivors)"
kill_survivors
if [ "$status2" -eq 0 ] || [ -z "$left2" ]; then
  echo "FAIL run 2: the mutation was not detected (exit=$status2, survivors=[${left2}])"
  exit 1
fi
echo "PASS run 2: mutation detected; survivors were: $(echo "$left2" | tr '\n' ' ')"
echo "All cleanup checks passed."
