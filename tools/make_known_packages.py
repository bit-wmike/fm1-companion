# SPDX-License-Identifier: GPL-3.0-only
"""Steckbriefe bekannter FM-1-Pakete erzeugen: fm1companion/data/known_packages.json

  python3 -I tools/make_known_packages.py PKGDIR [MELODEE_RC7.fwsc MELODEE_RC7-res.bin]

PKGDIR enthält zip_v15.fwsc, v16.fwsc, melodee.fwsc (1.5.1), sloop.fwsc, skiff.fwsc, jiant.fwsc (siehe
tests/test_flashmodel.py). Die Pakete selbst kommen nicht ins Repo – nur Prüfsummen, Längen und Layouts.
"""
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fm1companion.flash import flashmodel as m  # noqa: E402

LIST = [  # Datei, label, Name, Version (wie INFO sie meldet), Notiz
    ("zip_v15.fwsc", "v15", "Original-Firmware V15", "", "offizielles M-VAVE V15 (FM-1_firmware_15.zip = FM-1.fwsc)"),
    ("v16.fwsc", "v16", "Original-Firmware V16", "", "M-VAVE V16 (Oktober 2026); README von aroum nennt a21368e0… – "
                                                    "ungeklärt"),
    ("melodee.fwsc", "melodee_1.5.1", "Melodee 1.5.1", "Melodee 1.5.1", ""),
    ("sloop.fwsc", "sloop_2.8.3", "SLOOP 2.8.3", "SLOOP 2.8.3", ""),
    ("skiff.fwsc", "skiff_2.0.3", "Skiff 2.0.3 (standard)", "", ""),
    ("jiant.fwsc", "jiant_0.9.9", "JIANT FM 0.9.9", "", ""),
]


def entry(p, label, name, version, note):
    e = {"label": label, "name": name, "identity": p.identity, "version": version, "file_sha256": p.file_sha256,
         "flash_bytes": p.write_end, "head_sha256": p.head_sha256, "app_sha256": p.app_sha256,
         "family": m.classify_family(p), "layout": {k: [hex(a), hex(b)] for k, (a, b) in p.layout.items()}}
    if note:
        e["note"] = note
    if p.write_end > m.V15_END:   # V16: Erkennung „abgeschnitten“ und Reste in V15-Layout-Datenbereichen
        e["app_prefix"] = {hex(m.V15_END): m.sha(p.flash[m.APP_START:m.V15_END])}
        e["sectors"] = {hex(a): m.sha(p.flash[a:a + m.SECTOR]) for a in range(m.V15_END, p.write_end, m.SECTOR)}
        # je 512-Byte-Block (Anfragegröße des Loaders): erkennt halb geschriebene Sektoren auch ohne die Paketdatei
        e["blocks"] = {hex(a): m.block_hash(p.flash[a:a + m.BLOCK]) for a in range(m.V15_END, p.write_end, m.BLOCK)}
    return e


def main(argv):
    d = Path(argv[1])
    out = []
    for fn, label, name, version, note in LIST:
        out.append(entry(m.Package.load(d / fn), label, name, version, note))
    if len(argv) > 3:
        p = m.Package.load(argv[2])
        e = entry(p, "melodee_1.6.0-rc.7", "Melodee 1.6.0-rc.7", "Melodee 1.6.0-rc.7",
                  "SHA-256 laut SHA256SUMS des GitHub-Releases keremimo/melodee v1.6.0-rc.7")
        res = Path(argv[3]).read_bytes()
        magic, length, crc = struct.unpack_from("<3I", res)
        assert magic == m.RES_MAGIC
        e["resources"] = {"length": length, "crc": crc, "sha256": m.sha(res)}
        out.insert(3, e)
    heads = {m.FACTORY_HEAD_SHA256: "Bootkopf ab Werk (V13, V14, V15)"}
    for e in out:
        if e["head_sha256"] not in heads:
            heads[e["head_sha256"]] = ("Bootkopf aus V16" if e["label"] == "v16" else
                                       "Bootkopf der Community-Pakete (Melodee, SLOOP, Skiff, JIANT)")
    doc = {"schema": 1, "source": "tools/make_known_packages.py – nachgerechnet am 31.10.2026 mit den echten Paketen",
           "loader_sha256": "ef1f2db89b011fa53e71df363f0122de0ea896618c53fb53e8da676322004541",
           "loader_note": "ota.bin der Original-Pakete V13, V14, V15 und V16 ist byte-gleich",
           "heads": heads, "packages": out}
    Path(__file__).resolve().parent.parent.joinpath("fm1companion", "data", "known_packages.json").write_text(
        json.dumps(doc, indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(f"{len(out)} Pakete geschrieben")


if __name__ == "__main__":
    main(sys.argv)
