#!/usr/bin/env bash
# FM-1 Companion Suite entfernen (Benutzer-Installation aus install.sh oder dem .run-Installer).
#
#   ./uninstall.sh              App, Starter, Startmenü-Einträge, Symbole entfernen
#   ./uninstall.sh --all        zusätzlich Einstellungen und interne Daten (Firmware-Repos, Editor-Kopien,
#                               Zwischenspeicher, Protokolle) – fragt vorher nach
#
# Deine Daten im Hauptordner (Standard ~/FM-1) werden NIE gelöscht. Systempakete (apt) bleiben.
# Das Debian-Paket entfernst du mit: sudo apt remove fm1-companion
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=/usr/bin/python3; [[ -x "$PY" ]] || PY="$(command -v python3)"
CORE="$HERE/packaging/installer/core.py"
[[ -f "$CORE" ]] || CORE="$HERE/installer/core.py"          # Kopie in der installierten App

[[ -n "${FM1C_NO_PKILL:-}" ]] || pkill -u "$(id -u)" -f "python3 -m fm1companion" 2>/dev/null || true
ARGS=()
if [[ "${1:-}" == "--all" ]]; then
  echo "Entfernt werden außerdem Einstellungen, Geräteprofile, Firmware-Werkzeuge, Editor-Kopien, Zwischenspeicher, Protokolle."
  echo "Dein Hauptordner (Standard ~/FM-1) bleibt unangetastet."
  if [[ "${2:-}" == "--yes" ]]; then a=j; else read -r -p "Wirklich entfernen? [j/N] " a; fi
  [[ "$a" =~ ^[jJyY]$ ]] && ARGS=(--settings --data)
fi
"$PY" "$CORE" uninstall ${ARGS[@]+"${ARGS[@]}"}
echo "Systempakete (PyQt6, mido …) wurden nicht angefasst."
