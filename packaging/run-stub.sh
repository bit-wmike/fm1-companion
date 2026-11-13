#!/bin/sh
# FM-1 Companion Suite @VERSION@ – self-extracting installer
#   sh FM-1-Companion-Suite-@VERSION@.run                     install graphically
#   … --unattended [--prefix DIR] [--system] [--no-deps] [--desktop] [--demo-data] [--no-fluidsynth-fix]
#   … --extract DIR                                          only extract
#   … --check                                                verify the checksum
# Messages are English; German when the system language is German (LC_ALL / LC_MESSAGES / LANG start with "de").
PAYLOAD_SHA256="@SHA256@"
NEED_PKGS="@NEED@"
set -u
SELF="$0"
LINE=$(awk '/^__FM1_PAYLOAD_BELOW__$/ { print NR + 1; exit 0 }' "$SELF")
payload() { tail -n +"$LINE" "$SELF"; }
# language: say "English" "Deutsch" prints the text for the system language (msg: without newline)
case "${FM1C_LANG:-}" in ''|system) _lang="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" ;; *) _lang="$FM1C_LANG" ;; esac
case "$_lang" in [dD][eE]*) FM1C_DE=1 ;; *) FM1C_DE=0 ;; esac
msg() { if [ "$FM1C_DE" = 1 ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
say() { msg "$1" "${2:-$1}"; printf '\n'; }
usage() {
  say "FM-1 Companion Suite @VERSION@ – self-extracting installer
  sh $SELF                     install graphically
  … --unattended [--prefix DIR] [--system] [--no-deps] [--desktop] [--demo-data] [--no-fluidsynth-fix]
  … --extract DIR              only extract
  … --check                    verify the checksum" "FM-1 Companion Suite @VERSION@ – selbstentpackender Installer
  sh $SELF                     grafisch installieren
  … --unattended [--prefix DIR] [--system] [--no-deps] [--desktop] [--demo-data] [--no-fluidsynth-fix]
  … --extract DIR              nur entpacken
  … --check                    Prüfsumme kontrollieren"
}

UNATT=0; NODEPS=0; EXTRACT=""; ARGS=""
for a in "$@"; do
  case "$a" in
    --unattended) UNATT=1 ;;
    --no-deps) NODEPS=1 ;;
    --check)
      sum=$(payload | sha256sum | cut -d' ' -f1)
      [ "$sum" = "$PAYLOAD_SHA256" ] && { say "Checksum OK." "Prüfsumme in Ordnung."; exit 0; } || { say "Checksum WRONG – file is damaged." "Prüfsumme FALSCH – Datei beschädigt."; exit 1; } ;;
    --help|-h) usage; exit 0 ;;
  esac
done
case " $* " in *" --extract "*) EXTRACT=$(printf '%s\n' "$@" | awk 'f{print;exit} /^--extract$/{f=1}') ;; esac

if [ -n "$EXTRACT" ]; then
  mkdir -p "$EXTRACT" && payload | tar xzf - -C "$EXTRACT" && say "Extracted to $EXTRACT" "Entpackt nach $EXTRACT"; exit $?
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/fm1c-install.XXXXXX") || exit 1
trap 'rm -rf "$TMP"' EXIT INT TERM
sum=$(payload | sha256sum | cut -d' ' -f1)
if [ "$sum" != "$PAYLOAD_SHA256" ]; then say "The installation file is damaged (checksum). Please download it again." "Die Installationsdatei ist beschädigt (Prüfsumme). Bitte neu herunterladen."; exit 1; fi
payload | tar xzf - -C "$TMP" || { say "Extracting failed." "Entpacken fehlgeschlagen."; exit 1; }

PY=/usr/bin/python3
[ -x "$PY" ] || PY=$(command -v python3 || true)

ask() {  # ask graphically (kdialog/zenity) or in the terminal
  if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v kdialog >/dev/null; then kdialog --title "FM-1 Companion Suite" --yesno "$1"
  elif [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v zenity >/dev/null; then zenity --question --title "FM-1 Companion Suite" --width 420 --text "$1"
  elif [ -t 0 ]; then printf '%s %s ' "$1" "$(msg '[y/N]' '[j/N]')"; read -r r || r=""; case "$r" in [jJyY]|[yY][eE][sS]|[jJ][aA]) return 0 ;; *) return 1 ;; esac
  else return 1; fi
}

missing=""
if [ "$NODEPS" = 0 ] && command -v dpkg-query >/dev/null; then
  for p in $NEED_PKGS; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing="$missing $p"
  done
  if [ -n "$missing" ]; then
    if [ "$UNATT" = 1 ] || ask "$(msg "The FM-1 Companion Suite needs system packages that are missing:
$missing

Install them now? (asks for your password)" "Für die FM-1 Companion Suite fehlen Systempakete:
$missing

Jetzt installieren? (fragt nach deinem Passwort)")"; then
      if command -v pkexec >/dev/null && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        pkexec sh -c "apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y $missing"
      elif command -v sudo >/dev/null; then
        sudo sh -c "apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y $missing"
      else say "Please install as root: apt-get install$missing" "Bitte als root installieren: apt-get install$missing"; exit 1; fi || {
        say "The packages could not be installed." "Pakete ließen sich nicht installieren."; exit 1; }
    else
      say "Cancelled. Install them yourself with: sudo apt install$missing" "Abgebrochen. Selbst installieren mit: sudo apt install$missing"; exit 1
    fi
  fi
fi
[ -n "$PY" ] || { say "python3 is missing: sudo apt install python3" "python3 fehlt: sudo apt install python3"; exit 1; }

if [ "$UNATT" = 1 ]; then   # no exec: otherwise the trap above does not run and the extracted folder stays in /tmp
  "$PY" "$TMP/app/packaging/installer/gui.py" --source "$TMP/app" --python "$PY" "$@"
  exit $?
fi
if ! "$PY" -c "import PyQt6.QtWidgets" 2>/dev/null; then
  say "PyQt6 is missing – installing in the terminal without the graphical wizard." "PyQt6 fehlt – installiere im Terminal ohne grafischen Assistenten."
  "$PY" "$TMP/app/packaging/installer/core.py" install --source "$TMP/app" --python "$PY"; exit $?
fi
"$PY" "$TMP/app/packaging/installer/gui.py" --source "$TMP/app" --python "$PY" "$@"
exit $?
__FM1_PAYLOAD_BELOW__
