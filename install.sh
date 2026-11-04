#!/usr/bin/env bash
# Install the FM-1 Companion Suite – for your own user (the app itself needs no root).
#
#   ./install.sh            check/install system packages (asks for sudo), install the app
#   ./install.sh --no-apt   do not touch system packages, only copy the app
#   ./install.sh --desktop  also put a shortcut on the desktop
#   ./install.sh --no-fluidsynth-fix
#                           do NOT disable Debian's FluidSynth user service (default: disable it if active –
#                           otherwise it grabs the HDMI sound card at every login; clearly reported)
#   ./install.sh --help     show this help
#
# Graphical: FM-1-Companion-Suite-<version>.run, as a package: fm1-companion_<version>_all.deb
# (both under "Releases" on GitHub once published – or build them yourself, see README).
# Remove again: ./uninstall.sh  (or start menu › "Uninstall FM-1 Companion")
#
# Messages are English; German when the system language is German (LC_ALL / LC_MESSAGES / LANG start with "de";
# FM1C_LANG overrides it like in the app).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- language: msg "English" "Deutsch" prints the text for the system language ----
case "${FM1C_LANG:-}" in ''|system) _lang="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" ;; *) _lang="$FM1C_LANG" ;; esac
case "$_lang" in [dD][eE]*) FM1C_DE=1 ;; *) FM1C_DE=0 ;; esac
msg() { if ((FM1C_DE)); then printf '%s' "$2"; else printf '%s' "$1"; fi; }

usage() {
  if ((FM1C_DE)); then cat <<'HILFE'
FM-1 Companion Suite installieren – für den eigenen Benutzer (App selbst braucht kein Root).

  ./install.sh            Systempakete prüfen/installieren (fragt nach sudo), App installieren
  ./install.sh --no-apt   keine Systempakete anfassen, nur die App kopieren
  ./install.sh --desktop  zusätzlich Verknüpfung auf dem Schreibtisch
  ./install.sh --no-fluidsynth-fix
                          Debians FluidSynth-Benutzerdienst NICHT abschalten (Standard: abschalten, wenn aktiv –
                          er belegt sonst bei jeder Anmeldung die HDMI-Soundkarte; wird deutlich gemeldet)

Wieder entfernen: ./uninstall.sh  (oder Startmenü › „FM-1 Companion deinstallieren“)
HILFE
  else cat <<'HELP'
Install the FM-1 Companion Suite – for your own user (the app itself needs no root).

  ./install.sh            check/install system packages (asks for sudo), install the app
  ./install.sh --no-apt   do not touch system packages, only copy the app
  ./install.sh --desktop  also put a shortcut on the desktop
  ./install.sh --no-fluidsynth-fix
                          do NOT disable Debian's FluidSynth user service (default: disable it if active –
                          otherwise it grabs the HDMI sound card at every login; clearly reported)

Remove again: ./uninstall.sh  (or start menu › "Uninstall FM-1 Companion")
HELP
  fi
}

NO_APT=0; EXTRA=()
for a in "$@"; do
  case "$a" in
    --help|-h) usage; exit 0 ;;
    --no-apt) NO_APT=1 ;;
    --desktop|--demo-data|--no-menu|--no-fluidsynth-fix) EXTRA+=("$a") ;;
  esac
done

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m%s\033[0m %s\n' "$(msg "ERROR:" "FEHLER:")" "$*" >&2; exit 1; }

# ---- Important notice (the same text as in the app before the first installation/recovery) ----
if ((FM1C_DE)); then cat <<'HINWEIS'

  Wichtiger Hinweis
  -----------------
  Die FM-1 Companion Suite ist ein inoffizielles Community-Projekt und nicht mit M-VAVE verbunden.
  Firmware installieren, Rettung (Update-Modus, ROM-Bootmodus), Reste aufräumen und das Zurückschreiben
  aus dem Studio verändern dein Gerät. Das geschieht auf eigenes Risiko – im schlimmsten Fall startet das
  FM-1 nicht mehr und muss mit den Rettungswerkzeugen wiederbelebt werden.
  Mach vorher ein Backup und trenne das Gerät während eines Vorgangs nicht.
  Nicht jede Funktion ist auf echter Hardware getestet (siehe docs/UNTESTED.md).
  Es gibt keine Gewährleistung und keine Haftung, soweit gesetzlich zulässig
  (GNU GPL 3.0, Abschnitte 15 und 16). Vor der ersten Installation auf dem FM-1 fragt die App
  noch einmal („Ich habe verstanden“).

HINWEIS
else cat <<'NOTICE'

  Important notice
  ----------------
  FM-1 Companion Suite is an unofficial community project and not affiliated with M-VAVE.
  Installing firmware, recovery (update mode, ROM boot mode), cleaning up leftovers and writing back
  from the Studio change your device. You do this at your own risk – in the worst case the FM-1 no
  longer starts and has to be revived with the recovery tools.
  Make a backup first and do not disconnect the device during an operation.
  Not every function has been tested on real hardware (see docs/UNTESTED.md).
  There is no warranty and no liability, to the extent permitted by law
  (GNU GPL 3.0, sections 15 and 16). Before the first installation on the FM-1 the app asks
  once more ("I understand").

NOTICE
fi

# Always use the system Python: inside an active venv "python3" would not see the system packages.
PY=/usr/bin/python3
[[ -x "$PY" ]] || PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || die "$(msg "python3 is missing: sudo apt install python3" "python3 fehlt: sudo apt install python3")"
[[ -n "${VIRTUAL_ENV:-}" ]] && warn "$(msg "Active venv detected ($VIRTUAL_ENV) – the app uses $PY anyway." \
                                          "Aktives venv erkannt ($VIRTUAL_ENV) – die App nutzt trotzdem $PY.")"

# ---- 1. System packages (list: fm1companion/data/deps.json) ----
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
      warn "$(msg "Missing packages (not installed because of --no-apt): ${missing[*]}" \
                  "Fehlende Pakete (nicht installiert wegen --no-apt): ${missing[*]}")"
    else
      info "$(msg "Installing system packages: ${missing[*]}" "Installiere Systempakete: ${missing[*]}")"
      sudo apt-get update -q
      sudo apt-get install -y "${NEED[@]}" || die "$(msg "Required packages could not be installed" \
                                                         "Pflicht-Pakete ließen sich nicht installieren")"
      # one by one, so that a recommended package missing on this release does not abort everything
      for p in "${WANT[@]}"; do
        sudo apt-get install -y "$p" >/dev/null 2>&1 || warn "$(msg "optional, not available: $p" "optional, nicht verfügbar: $p")"
      done
    fi
  else
    ok "$(msg "All system packages are present" "Alle Systempakete sind da")"
  fi
else
  warn "$(msg "Not a Debian system – please install these yourself: ${NEED[*]}" \
              "Kein Debian-System – bitte selbst installieren: ${NEED[*]}")"
fi

# ---- 2. Install the app (same core as the graphical installer) ----
info "$(msg "Installing the app" "Installiere die App")"
"$PY" "$HERE/packaging/installer/core.py" install --source "$HERE" --python "$PY" ${EXTRA[@]+"${EXTRA[@]}"}
BIN="$HOME/.local/bin/fm1-companion"
[[ " ${EXTRA[*]-} " == *" --no-menu "* ]] || ok "$(msg "Start menu entry 'FM-1 Companion' created (Multimedia)" \
                                                       "Startmenü-Eintrag 'FM-1 Companion' angelegt (Multimedia)")"

# ---- 3. Check ----
info "$(msg "Checking the installation" "Prüfe die Installation")"
if "$BIN" --check; then
  ok "$(msg "Done. Start it from the start menu or with: fm1-companion" "Fertig. Starten über das Startmenü oder mit: fm1-companion")"
  ok "$(msg "Try it without a device: fm1-companion --demo" "Ohne Gerät ausprobieren: fm1-companion --demo")"
else
  warn "$(msg "The app starts, but something is still missing (see above)." "Die App startet, aber es fehlt noch etwas (siehe oben).")"
fi
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *)
  warn "$(msg "$HOME/.local/bin is not in PATH – the start menu works anyway." \
              "$HOME/.local/bin ist nicht im PATH – das Startmenü funktioniert trotzdem.")";; esac
