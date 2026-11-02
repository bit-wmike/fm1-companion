# SPDX-License-Identifier: GPL-3.0-only
"""Zuordnung freedesktop-/Breeze-Symbolnamen -> Lucide-Symbol (ISC-Lizenz, siehe LICENSE.lucide).

Seiten rufen ``common.icon("document-save", "backup")`` mit Namen aus dem Desktop-Thema auf;
die App zeichnet daraus ein einheitliches Lucide-Symbol in der Textfarbe des aktuellen Designs.
Ein Eintrag kann eine Tönung haben: "name:tint" mit tint = accent | danger | warning | success.
"""
MAP = {
    # Vergleich / Musik (von späteren Bereichen benutzt)
    "document-compare": "columns-3", "kompare": "columns-3", "view-split-left-right": "columns-3", "roll": "piano",
    # Navigation / Dateien
    "go-home": "house", "user-home": "house", "go-next": "arrow-right", "go-previous": "arrow-left",
    "go-up": "arrow-up", "go-down": "arrow-down", "go-first": "chevrons-left", "go-last": "chevrons-right",
    "go-jump": "corner-down-right", "folder": "folder", "folder-open": "folder-open", "folder-new": "folder-plus",
    "folder-music": "folder-heart", "folder-sound": "folder-heart", "folder-download": "folder-down",
    "folder-documents": "folder", "folder-remote": "folder-sync", "inode-directory": "folder",
    "document-new": "file-plus", "document-open": "file-input", "document-open-recent": "history",
    "document-save": "save", "document-save-as": "save", "document-save-all": "save-all", "backup": "archive",
    "document-revert": "undo-2", "document-edit": "pencil", "document-export": "file-output",
    "document-import": "file-input", "document-send": "send", "document-properties": "file-cog",
    "document-print": "printer", "document-preview": "eye", "document-close": "x", "text-x-generic": "file-text",
    "application-x-firmware": "cpu", "application-json": "file-json", "text-markdown": "file-text",
    "application-zip": "file-archive", "package-x-generic": "package", "download": "download",
    "upload-media": "upload", "cloud-download": "cloud-download", "cloud-upload": "cloud-upload",
    "edit-copy": "copy", "edit-cut": "scissors", "edit-paste": "clipboard-paste", "edit-delete": "trash-2:danger",
    "edit-clear": "eraser", "edit-clear-all": "eraser", "edit-rename": "pencil-line", "edit-undo": "undo-2",
    "edit-redo": "redo-2", "edit-find": "search", "edit-select-all": "square-check", "system-search": "search",
    "list-add": "plus", "list-remove": "minus", "edit-find-replace": "replace", "insert-text": "type",
    "bookmark-new": "bookmark-plus", "bookmarks": "bookmark", "tag": "tag", "starred-symbolic": "star",
    "emblem-favorite": "star", "rating": "star", "favorite": "star", "starred": "star",
    "non-starred": "star", "emblem-ok": "circle-check:success", "emblem-important": "circle-alert:warning",
    "emblem-locked": "lock", "emblem-unlocked": "lock-open", "emblem-shared": "share-2",
    # Ansicht / Fenster
    "view-refresh": "refresh-cw", "view-list-details": "list", "view-list-tree": "list-tree",
    "view-list-icons": "layout-grid", "view-grid": "layout-grid", "view-filter": "list-filter",
    "view-sort": "arrow-up-down", "view-sort-ascending": "arrow-down-a-z", "view-preview": "eye",
    "view-hidden": "eye-off", "view-visible": "eye", "view-fullscreen": "maximize", "view-restore": "minimize",
    "view-sidebar": "panel-left", "sidebar-show": "panel-left", "zoom-in": "zoom-in", "zoom-out": "zoom-out",
    "zoom-original": "scan", "zoom-fit-best": "scan", "transform-scale": "scaling", "window-close": "x",
    "window-new": "app-window", "view-calendar": "calendar", "view-media-equalizer": "sliders-vertical",
    "view-statistics": "chart-column", "office-chart-line": "chart-line", "office-chart-bar": "chart-column",
    "format-list-unordered": "list", "format-text-bold": "bold", "code-context": "code",
    # Aktionen / System
    "configure": "settings", "preferences-system": "settings", "preferences-other": "settings-2",
    "preferences-desktop": "monitor-cog", "preferences-desktop-theme": "palette",
    "preferences-desktop-color": "palette", "color-management": "palette", "preferences-desktop-font": "type",
    "preferences-desktop-notification": "bell", "notifications": "bell", "notification-active": "bell-ring",
    "application-exit": "log-out", "system-shutdown": "power", "system-reboot": "rotate-ccw",
    "system-run": "play", "run-build": "hammer", "tools": "wrench", "tools-wizard": "wand-sparkles",
    "applications-development": "code-xml", "applications-utilities": "wrench",
    "applications-system": "monitor-cog", "applications-multimedia": "clapperboard",
    "applications-games": "gamepad-2", "applications-internet": "globe", "applications-science": "flask-conical",
    "applications-education": "graduation-cap", "applications-graphics": "image",
    "utilities-terminal": "square-terminal", "utilities-system-monitor": "activity",
    "system-software-update": "circle-arrow-up", "update-none": "circle-arrow-up", "update-high": "circle-arrow-up:accent",
    "system-software-install": "package-plus", "install": "package-plus", "uninstall": "package-minus",
    "system-help": "life-buoy", "help-contents": "book-open", "help-about": "info", "help-browser": "circle-help",
    "help-faq": "message-circle-question", "help-hint": "lightbulb", "help-keybord-shortcuts": "keyboard",
    "help-keyboard-shortcuts": "keyboard", "dialog-information": "info", "dialog-warning": "triangle-alert:warning",
    "dialog-error": "circle-x:danger", "dialog-ok": "check", "dialog-ok-apply": "check", "dialog-cancel": "x",
    "dialog-close": "x", "dialog-question": "circle-help", "data-warning": "triangle-alert:warning",
    "data-error": "circle-x:danger", "data-information": "info", "data-success": "circle-check:success",
    "security-high": "shield-check:success", "security-medium": "shield-alert:warning",
    "security-low": "shield-x:danger", "lock": "lock", "unlock": "lock-open", "process-stop": "octagon-x:danger",
    "media-flash": "zap", "flash": "zap", "cpu": "cpu", "chip": "cpu", "computer": "monitor", "hwinfo": "cpu",
    "drive-harddisk": "hard-drive", "drive-removable-media": "usb", "drive-removable-media-usb": "usb",
    "device-usb": "usb", "network-wired": "network", "network-connect": "plug-zap", "network-disconnect": "unplug",
    "network-wireless": "wifi", "internet-web-browser": "globe", "globe": "globe", "link": "link",
    "insert-link": "link", "external-link": "external-link", "mail-send": "send", "mail-message-new": "mail",
    "clock": "clock", "chronometer": "timer", "appointment-new": "calendar-plus", "history": "history",
    "user-identity": "user", "im-user": "user", "system-users": "users", "battery": "battery",
    "temperature-normal": "thermometer", "sensors-temperature-symbolic": "thermometer", "fan": "fan",
    "memory": "memory-stick", "power": "power", "plugin": "puzzle", "extension": "puzzle", "games-hint": "lightbulb",
    "object-locked": "lock", "media-optical": "disc", "camera-photo": "camera", "image-x-generic": "image",
    "video-x-generic": "file-video", "x-office-document": "file-text", "accessories-text-editor": "file-pen",
    "accessories-calculator": "calculator", "fork": "git-fork", "vcs-branch": "git-branch",
    "git": "git-branch", "repository": "git-branch", "code-block": "braces", "debug-run": "bug",
    "tools-report-bug": "bug", "kbugbuster": "bug", "flag": "flag", "pin": "pin", "pan": "hand",
    "search": "search", "filter": "list-filter", "trash-empty": "trash-2", "user-trash": "trash-2",
    "checkmark": "check", "object-select": "check", "draw-arrow-forward": "arrow-right",
    "arrow-right": "arrow-right", "arrow-left": "arrow-left", "go-next-view": "chevron-right",
    "go-previous-view": "chevron-left", "overflow-menu": "ellipsis", "application-menu": "menu",
    "open-menu": "menu", "settings-configure": "settings", "keyboard": "keyboard", "input-keyboard": "keyboard",
    "input-mouse": "mouse", "input-gaming": "gamepad-2", "input-tablet": "tablet", "smartphone": "smartphone",
    "scanner": "scan-line", "sync": "refresh-cw", "state-sync": "refresh-cw", "state-ok": "circle-check:success",
    "state-warning": "triangle-alert:warning", "state-error": "circle-x:danger", "news-subscribe": "rss",
    "news": "newspaper", "akregator": "newspaper", "compass": "compass", "map-flat": "map", "explore": "compass",
    "discover": "compass", "plasmadiscover": "compass", "shopping-cart": "shopping-cart", "rocket": "rocket",
    "favorites": "star", "games-config-options": "sliders-horizontal", "configure-shortcuts": "keyboard",
    "dashboard-show": "layout-dashboard", "kpi": "gauge", "speedometer": "gauge", "office-report": "file-chart-column",
    # Audio / Musik
    "audio-card": "audio-lines", "audio-midi": "piano", "audio-x-midi": "music", "midi": "piano",
    "audio-x-generic": "file-audio", "audio-x-wav": "file-audio", "audio-input-microphone": "mic",
    "audio-headphones": "headphones", "audio-speakers": "speaker", "audio-volume-high": "volume-2",
    "audio-volume-medium": "volume-1", "audio-volume-low": "volume", "audio-volume-muted": "volume-x",
    "media-record": "circle-dot:danger", "media-playback-start": "play", "media-playback-pause": "pause",
    "media-playback-stop": "square", "media-skip-forward": "skip-forward", "media-skip-backward": "skip-back",
    "media-seek-forward": "fast-forward", "media-seek-backward": "rewind", "media-repeat-all": "repeat",
    "media-repeat-single": "repeat-1", "media-playlist-shuffle": "shuffle", "media-playlist-repeat": "repeat",
    "audio-ready": "audio-lines", "player-volume": "volume-2", "music-note-16th": "music-2",
    "view-media-playlist": "list-music", "audacity": "audio-waveform", "audio-waveform": "audio-waveform",
    "piano": "piano", "keyboard-music": "keyboard-music", "drum": "drum", "metronome": "timer",
    "guitar": "guitar", "radio": "radio", "media-tape": "cassette-tape", "multimedia-player": "disc-3",
    "sound": "volume-2", "waveform": "audio-waveform", "sliders": "sliders-horizontal", "knob": "circle-gauge",
    "synth": "audio-lines", "oscillator": "activity", "music": "music", "voice": "mic-vocal", "speaker": "speaker",
    "simulator": "monitor-play", "emulator": "monitor-play", "presets": "library", "library": "library",
    "compose": "pen-tool", "fm1-device": "keyboard-music",
}

# Lucide-Namen, die ohne Zuordnung direkt benutzt werden dürfen (icon("piano"))
EXTRA = ["layers", "box", "boxes", "sparkles", "wand", "bell-off", "circle-help", "circle", "circle-dot",
         "chevron-down", "chevron-up", "chevron-left", "chevron-right", "check", "x", "info", "triangle-alert",
         "circle-check", "circle-x", "octagon-x", "copy", "save", "settings", "command", "external-link",
         "life-buoy", "heart", "thermometer", "gauge", "hard-drive", "memory-stick", "cpu", "activity",
         "folder-tree", "arrow-up-right", "list-checks", "file-search", "scroll-text", "badge-info",
         "panel-left-close", "panel-left-open", "maximize-2", "minimize-2", "sun", "moon", "sun-moon",
         "app-window", "laptop", "server", "terminal", "list-music", "music-4", "file-music", "disc",
         "sliders-vertical", "waves", "audio-waveform", "repeat-2", "timer-reset", "undo", "redo",
         "package", "package-check", "shield", "shield-check", "plug", "unplug", "usb", "bug", "lightbulb",
         "book", "book-open-text", "graduation-cap", "map", "route", "zap-off", "battery-warning",
         "battery-low", "cable", "power-off", "square-play", "file-down", "file-up", "trash", "star-off",
         "rotate-cw", "scissors", "split", "merge", "combine", "grid-3x3", "table", "columns-3", "hash",
         "at-sign", "rss", "newspaper", "message-square", "user-round", "folder-git-2"]


def lucide_name(name: str) -> tuple[str, str]:
    """(lucide-name, tint) oder ("", "") wenn unbekannt."""
    v = MAP.get(name)
    if v is None:
        base = name[:-9] if name.endswith("-symbolic") else name
        v = MAP.get(base)
        if v is None and base != name:
            name = base
    if v is None:
        return "", ""
    n, _, tint = v.partition(":")
    return n, tint
