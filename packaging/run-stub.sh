#!/bin/sh
# FM-1 Companion Suite @VERSION@ – selbstentpackender Installer
#   sh FM-1-Companion-Suite-@VERSION@.run                     grafisch installieren
#   … --unattended [--prefix DIR] [--system] [--no-deps] [--desktop] [--demo-data] [--no-fluidsynth-fix]
#   … --extract DIR                                          nur entpacken
#   … --check                                                Prüfsumme kontrollieren
PAYLOAD_SHA256="@SHA256@"
NEED_PKGS="@NEED@"
set -u
SELF="$0"
LINE=$(awk '/^__FM1_PAYLOAD_BELOW__$/ { print NR + 1; exit 0 }' "$SELF")
payload() { tail -n +"$LINE" "$SELF"; }
say() { printf '%s\n' "$*"; }

UNATT=0; NODEPS=0; EXTRACT=""; ARGS=""
for a in "$@"; do
  case "$a" in
    --unattended) UNATT=1 ;;
    --no-deps) NODEPS=1 ;;
    --check)
      sum=$(payload | sha256sum | cut -d' ' -f1)
      [ "$sum" = "$PAYLOAD_SHA256" ] && { say "Prüfsumme in Ordnung."; exit 0; } || { say "Prüfsumme FALSCH – Datei beschädigt."; exit 1; } ;;
    --help|-h) sed -n '2,6p' "$SELF"; exit 0 ;;
  esac
done
case " $* " in *" --extract "*) EXTRACT=$(printf '%s\n' "$@" | awk 'f{print;exit} /^--extract$/{f=1}') ;; esac

if [ -n "$EXTRACT" ]; then
  mkdir -p "$EXTRACT" && payload | tar xzf - -C "$EXTRACT" && say "Entpackt nach $EXTRACT"; exit $?
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/fm1c-install.XXXXXX") || exit 1
trap 'rm -rf "$TMP"' EXIT INT TERM
sum=$(payload | sha256sum | cut -d' ' -f1)
if [ "$sum" != "$PAYLOAD_SHA256" ]; then say "Die Installationsdatei ist beschädigt (Prüfsumme). Bitte neu herunterladen."; exit 1; fi
payload | tar xzf - -C "$TMP" || { say "Entpacken fehlgeschlagen."; exit 1; }

PY=/usr/bin/python3
[ -x "$PY" ] || PY=$(command -v python3 || true)

ask() {  # Frage grafisch (kdialog/zenity) oder im Terminal
  if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v kdialog >/dev/null; then kdialog --title "FM-1 Companion Suite" --yesno "$1"
  elif [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v zenity >/dev/null; then zenity --question --title "FM-1 Companion Suite" --width 420 --text "$1"
  elif [ -t 0 ]; then printf '%s [j/N] ' "$1"; read -r r; case "$r" in j|J|y|Y) return 0 ;; *) return 1 ;; esac
  else return 1; fi
}

missing=""
if [ "$NODEPS" = 0 ] && command -v dpkg-query >/dev/null; then
  for p in $NEED_PKGS; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing="$missing $p"
  done
  if [ -n "$missing" ]; then
    if [ "$UNATT" = 1 ] || ask "Für die FM-1 Companion Suite fehlen Systempakete:
$missing

Jetzt installieren? (fragt nach deinem Passwort)"; then
      if command -v pkexec >/dev/null && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        pkexec sh -c "apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y $missing"
      elif command -v sudo >/dev/null; then
        sudo sh -c "apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y $missing"
      else say "Bitte als root installieren: apt-get install$missing"; exit 1; fi || { say "Pakete ließen sich nicht installieren."; exit 1; }
    else
      say "Abgebrochen. Selbst installieren mit: sudo apt install$missing"; exit 1
    fi
  fi
fi
[ -n "$PY" ] || { say "python3 fehlt: sudo apt install python3"; exit 1; }

if [ "$UNATT" = 1 ]; then   # kein exec: sonst läuft die Falle oben nicht und der entpackte Ordner bleibt in /tmp
  "$PY" "$TMP/app/packaging/installer/gui.py" --source "$TMP/app" --python "$PY" "$@"
  exit $?
fi
if ! "$PY" -c "import PyQt6.QtWidgets" 2>/dev/null; then
  say "PyQt6 fehlt – installiere im Terminal ohne grafischen Assistenten."
  "$PY" "$TMP/app/packaging/installer/core.py" install --source "$TMP/app" --python "$PY"; exit $?
fi
"$PY" "$TMP/app/packaging/installer/gui.py" --source "$TMP/app" --python "$PY" "$@"
exit $?
__FM1_PAYLOAD_BELOW__
