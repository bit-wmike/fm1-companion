#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Run the UI test scripts (tests/ui_*_test.py and tests/editor_*_test.py), one after the other, each in its own
# process, with a time limit. Prints a summary and exits non-zero if any script failed or ran out of time.
#
#   tests/run_ui.sh                    all scripts (about 20 minutes on a Raspberry Pi 5)
#   tests/run_ui.sh ui_flow ui_shell   only scripts whose file name contains one of the words
#   tests/run_ui.sh --shard 1/3        the first third of the list (for parallel jobs: 1/3, 2/3, 3/3)
#   tests/run_ui.sh --timeout 600      seconds per script (default 900; or FM1_UI_TIMEOUT)
#   tests/run_ui.sh --list             only print which scripts would run
#
# The scripts need no display (QT_QPA_PLATFORM=offscreen is set when nothing else is), no FM-1 and no network; each
# one uses its own temporary home folder. As root (containers) Qt WebEngine needs QTWEBENGINE_DISABLE_SANDBOX=1,
# which is set here for root. tests/editor_bridge_test.py needs the web editor from a Melodee checkout: give its
# path in FM1_MELODEE_EDITOR, otherwise the script is skipped (and listed as skipped).
set -u
cd "$(dirname "$0")/.."

TIMEOUT="${FM1_UI_TIMEOUT:-900}"; SHARD=""; LIST=0; WORDS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --timeout) TIMEOUT="${2:?--timeout needs seconds}"; shift ;;
    --shard) SHARD="${2:?--shard needs I/N}"; shift ;;
    --list) LIST=1 ;;
    -h|--help) sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option: $1 (see tests/run_ui.sh --help)" >&2; exit 2 ;;
    *) WORDS+=("$1") ;;
  esac
  shift
done

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
export PYTHONDONTWRITEBYTECODE=1
[ "$(id -u)" = 0 ] && export QTWEBENGINE_DISABLE_SANDBOX="${QTWEBENGINE_DISABLE_SANDBOX:-1}"
PY="${PYTHON:-python3}"

all=()
for f in tests/ui_*_test.py tests/editor_*_test.py; do
  [ -f "$f" ] || continue
  if [ ${#WORDS[@]} -gt 0 ]; then
    hit=0; for w in "${WORDS[@]}"; do case "$(basename "$f")" in *"$w"*) hit=1 ;; esac; done
    [ $hit = 1 ] || continue
  fi
  all+=("$f")
done
if [ -n "$SHARD" ]; then
  i="${SHARD%/*}"; n="${SHARD#*/}"
  case "$i$n" in *[!0-9]*|"") echo "--shard needs I/N, e.g. 1/3" >&2; exit 2 ;; esac
  [ "$i" -ge 1 ] && [ "$i" -le "$n" ] || { echo "--shard $SHARD: I must be 1…N" >&2; exit 2; }
  pick=(); k=0
  for f in "${all[@]}"; do [ $((k % n)) = $((i - 1)) ] && pick+=("$f"); k=$((k + 1)); done
  all=("${pick[@]+"${pick[@]}"}")
fi
[ ${#all[@]} -gt 0 ] || { echo "no UI script matches" >&2; exit 2; }
if [ $LIST = 1 ]; then printf '%s\n' "${all[@]}"; exit 0; fi

group() { [ -n "${GITHUB_ACTIONS:-}" ] && echo "::group::$1" || echo "===== $1"; }
endgroup() { [ -n "${GITHUB_ACTIONS:-}" ] && echo "::endgroup::"; return 0; }

rows=(); failed=0; passed=0; skipped=0; t_all=$(date +%s)
for f in "${all[@]}"; do
  args=()
  if [ "$(basename "$f")" = editor_bridge_test.py ]; then
    ed="${FM1_MELODEE_EDITOR:-}"
    if [ -z "$ed" ] || [ ! -f "$ed" ]; then
      echo "skipped: $f (needs the web editor of a Melodee checkout: FM1_MELODEE_EDITOR=/path/to/melodee/web/editor.html)"
      rows+=("skipped|$f|–|needs FM1_MELODEE_EDITOR"); skipped=$((skipped + 1)); continue
    fi
    args=("$ed")
  fi
  group "$f"
  t0=$(date +%s)
  log=$(mktemp)
  # -k: a script that ignores the first signal is killed 20 s later
  timeout -k 20 "$TIMEOUT" "$PY" "$f" ${args[@]+"${args[@]}"} 2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  dt=$(( $(date +%s) - t0 ))
  last=$(grep -E '[0-9]+/[0-9]+ (passed|bestanden)' "$log" | tail -1 | tr -d '|' | cut -c1-60)
  rm -f "$log"
  endgroup
  if [ "$rc" = 0 ]; then
    echo "ok: $f (${dt} s)"; rows+=("ok|$f|${dt} s|$last"); passed=$((passed + 1))
  else
    why="exit code $rc"
    [ "$rc" = 124 ] || [ "$rc" = 137 ] && why="no result after ${TIMEOUT} s (stopped)"
    [ "$rc" = 139 ] && why="crashed (segmentation fault)"
    [ -n "${GITHUB_ACTIONS:-}" ] && echo "::error title=UI script failed::$f: $why"
    echo "FAILED: $f – $why (${dt} s)"; rows+=("FAILED|$f|${dt} s|$why${last:+; $last}"); failed=$((failed + 1))
  fi
done

total=$(( $(date +%s) - t_all ))
echo
echo "UI scripts: $passed passed, $failed failed, $skipped skipped, ${total} s"
for r in "${rows[@]}"; do IFS='|' read -r st f dt note <<<"$r"; printf '  %-8s %-34s %8s  %s\n' "$st" "$f" "$dt" "$note"; done
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### UI scripts${FM1_UI_LABEL:+ – $FM1_UI_LABEL}: $passed passed, $failed failed, $skipped skipped"
    echo
    echo "| Result | Script | Time | Note |"
    echo "|---|---|---|---|"
    for r in "${rows[@]}"; do IFS='|' read -r st f dt note <<<"$r"; echo "| $st | \`$f\` | $dt | $note |"; done
    echo
  } >> "$GITHUB_STEP_SUMMARY"
fi
[ "$failed" = 0 ]
