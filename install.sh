#!/usr/bin/env bash
# FM-1 Companion Suite installieren – für den eigenen Benutzer (App selbst braucht kein Root).
#
#   ./install.sh            Systempakete prüfen/installieren (fragt nach sudo), App installieren
#   ./install.sh --no-apt   keine Systempakete anfassen, nur die App kopieren
#   ./install.sh --desktop  zusätzlich Verknüpfung auf dem Schreibtisch
#   ./install.sh --no-fluidsynth-fix
#                           Debians FluidSynth-Benutzerdienst NICHT abschalten (Standard: abschalten, wenn aktiv –
#                           er belegt sonst bei jeder Anmeldung die HDMI-Soundkarte; wird deutlich gemeldet)
#
# Grafisch geht es mit FM-1-Companion-Suite-<version>.run, als Paket mit fm1-companion_<version>_all.deb
# (beides unter „Releases“ auf GitHub, sobald veröffentlicht – oder selbst bauen, siehe README).
# Wieder entfernen: ./uninstall.sh  (oder Startmenü › „FM-1 Companion deinstallieren“)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NO_APT=0; EXTRA=()
for a in "$@"; do
  case "$a" in
    --no-apt) NO_APT=1 ;;
    --desktop|--demo-data|--no-menu|--no-fluidsynth-fix) EXTRA+=("$a") ;;
  esac
done

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mFEHLER:\033[0m %s\n' "$*" >&2; exit 1; }

# ---- Wichtiger Hinweis (derselbe Text wie in der App vor der ersten Installation/Rettung) ----
cat <<'HINWEIS'

  Wichtiger Hinweis
  -----------------
  Die FM-1 Companion Suite ist ein inoffizielles Community-Projekt und nicht mit M-VAVE verbunden.
  Firmware installieren, Rettung (Update-Modus, ROM-Bootmodus), Reste aufräumen und das Zurückschreiben
  aus dem Studio verändern dein Gerät. Das geschieht auf eigenes Risiko – im schlimmsten Fall startet das
  FM-1 nicht mehr und muss mit den Rettungswerkzeugen wiederbelebt werden.
  Mach vorher ein Backup und trenne das Gerät während eines Vorgangs nicht.
  Nicht jede Funktion ist auf echter Hardware getestet (siehe docs/UNGETESTET.md).
  Es gibt keine Gewährleistung und keine Haftung, soweit gesetzlich zulässig
  (GNU GPL 3.0, Abschnitte 15 und 16). Vor der ersten Installation auf dem FM-1 fragt die App
  noch einmal („Ich habe verstanden“).

HINWEIS

# Immer das System-Python benutzen: in einem aktiven venv sähe "python3" die Systempakete nicht.
PY=/usr/bin/python3
[[ -x "$PY" ]] || PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || die "python3 fehlt: sudo apt install python3"
[[ -n "${VIRTUAL_ENV:-}" ]] && warn "Aktives venv erkannt ($VIRTUAL_ENV) – die App nutzt trotzdem $PY."

# ---- 1. Systempakete (Liste: fm1companion/data/deps.json) ----
pkgs() { "$PY" -c 'import json,sys; print(" ".join(d["package"] for d in json.load(open(sys.argv[1]))[sys.argv[2]]))' \
           "$HERE/fm1companion/data/deps.json" "$1" 2>/dev/null; }
read -r -a NEED <<< "$(pkgs required || echo python3 python3-pyqt6 python3-pyqt6.qtsvg python3-pyqt6.qtwebengine python3-pyqt6.qtwebchannel python3-mido python3-rtmidi python3-numpy git)"
read -r -a WANT <<< "$(pkgs recommended || echo python3-pyqt6.qtmultimedia ffmpeg alsa-utils pipewire-bin)"

if command -v dpkg-query >/dev/null; then
  missing=()
  for p in "${NEED[@]}" "${WANT[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing+=("$p")
  done
  if ((${#missing[@]})); then
    if ((NO_APT)); then
      warn "Fehlende Pakete (nicht installiert wegen --no-apt): ${missing[*]}"
    else
      info "Installiere Systempakete: ${missing[*]}"
      sudo apt-get update -q
      sudo apt-get install -y "${NEED[@]}" || die "Pflicht-Pakete ließen sich nicht installieren"
      # einzeln, damit ein auf dieser Version fehlendes Wunschpaket nicht alles abbricht
      for p in "${WANT[@]}"; do sudo apt-get install -y "$p" >/dev/null 2>&1 || warn "optional, nicht verfügbar: $p"; done
    fi
  else
    ok "Alle Systempakete sind da"
  fi
else
  warn "Kein Debian-System – bitte selbst installieren: ${NEED[*]}"
fi

# ---- 2. App installieren (gleicher Kern wie der grafische Installer) ----
info "Installiere die App"
"$PY" "$HERE/packaging/installer/core.py" install --source "$HERE" --python "$PY" ${EXTRA[@]+"${EXTRA[@]}"}
BIN="$HOME/.local/bin/fm1-companion"
[[ " ${EXTRA[*]-} " == *" --no-menu "* ]] || ok "Startmenü-Eintrag 'FM-1 Companion' angelegt (Multimedia)"

# ---- 3. Prüfen ----
info "Prüfe die Installation"
if "$BIN" --check; then
  ok "Fertig. Starten über das Startmenü oder mit: fm1-companion"
  ok "Ohne Gerät ausprobieren: fm1-companion --demo"
else
  warn "Die App startet, aber es fehlt noch etwas (siehe oben)."
fi
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "$HOME/.local/bin ist nicht im PATH – das Startmenü funktioniert trotzdem.";; esac
