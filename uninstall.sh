#!/usr/bin/env bash
# Remove the FM-1 Companion Suite (user installation from install.sh or the .run installer).
#
#   ./uninstall.sh              remove app, launcher, start menu entries, icons
#   ./uninstall.sh --all        also settings and internal data (firmware repos, editor copies,
#                               cache, logs) – asks first
#   ./uninstall.sh --help       show this help
#
# Your data in the main folder (default ~/FM-1) is NEVER deleted. System packages (apt) stay.
# Remove the Debian package with: sudo apt remove fm1-companion
# Messages are English; German when the system language is German (LC_ALL / LC_MESSAGES / LANG start with "de").
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- language: msg "English" "Deutsch" prints the text for the system language ----
case "${FM1C_LANG:-}" in ''|system) _lang="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" ;; *) _lang="$FM1C_LANG" ;; esac
case "$_lang" in [dD][eE]*) FM1C_DE=1 ;; *) FM1C_DE=0 ;; esac
msg() { if ((FM1C_DE)); then printf '%s' "$2"; else printf '%s' "$1"; fi; }

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  msg "Remove the FM-1 Companion Suite (user installation).

  ./uninstall.sh              remove app, launcher, start menu entries, icons
  ./uninstall.sh --all        also settings and internal data (firmware repos, editor copies,
                              cache, logs) – asks first

Your main folder (default ~/FM-1) is never deleted. System packages (apt) stay.
" "FM-1 Companion Suite entfernen (Benutzer-Installation).

  ./uninstall.sh              App, Starter, Startmenü-Einträge, Symbole entfernen
  ./uninstall.sh --all        zusätzlich Einstellungen und interne Daten (Firmware-Repos, Editor-Kopien,
                              Zwischenspeicher, Protokolle) – fragt vorher nach

Dein Hauptordner (Standard ~/FM-1) wird nie gelöscht. Systempakete (apt) bleiben.
"
  exit 0
fi

PY=/usr/bin/python3; [[ -x "$PY" ]] || PY="$(command -v python3)"
CORE="$HERE/packaging/installer/core.py"
[[ -f "$CORE" ]] || CORE="$HERE/installer/core.py"          # copy inside the installed app

[[ -n "${FM1C_NO_PKILL:-}" ]] || pkill -u "$(id -u)" -f "python3 -m fm1companion" 2>/dev/null || true
ARGS=()
if [[ "${1:-}" == "--all" ]]; then
  msg "--all would also remove: settings, device profiles, firmware tools, editor copies, cache, logs." \
      "--all würde außerdem entfernen: Einstellungen, Geräteprofile, Firmware-Werkzeuge, Editor-Kopien, Zwischenspeicher, Protokolle."
  echo
  msg "Your main folder (default ~/FM-1) is left untouched." "Dein Hauptordner (Standard ~/FM-1) bleibt unangetastet."
  echo
  # no answer (Enter, end of input, no terminal) means no: the app is removed, settings and data stay
  if [[ "${2:-}" == "--yes" ]]; then a=y
  else read -r -p "$(msg "Remove settings and internal data too? [y/N] " "Einstellungen und interne Daten auch entfernen? [j/N] ")" a || a=""; fi
  a="$(printf '%s' "$a" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  case "$a" in
    y|yes|j|ja)
      ARGS=(--settings --data)
      msg "Removing the app, settings and internal data." "Entferne App, Einstellungen und interne Daten." ;;
    *)
      msg "Settings and internal data are kept; only the app is removed." \
          "Einstellungen und interne Daten bleiben erhalten; nur die App wird entfernt." ;;
  esac
  echo
fi
"$PY" "$CORE" uninstall ${ARGS[@]+"${ARGS[@]}"}
msg "System packages (PyQt6, mido …) were not touched." "Systempakete (PyQt6, mido …) wurden nicht angefasst."
echo
