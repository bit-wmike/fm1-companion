# SPDX-License-Identifier: GPL-3.0-only
"""Prüfsummen der Werkzeuge, die die Rettung (Stufe 2) mit Root-Rechten ausführt – fest in die App eingebaut.

Vor jedem Start als root kopiert der pkexec-Befehl genau diese Dateien in ein root-eigenes Temp-Verzeichnis,
prüft jede gegen diese Liste und führt nur die geprüfte Kopie aus (nie Code aus beschreibbaren Ordnern).
  * unbricker: vendor/unbricker (MvaveFM1Unbricker 707470a, MIT)
  * jl: kagaimiqs jl-uboot-tool am festen Stand adb3f18 (MIT), ohne docs/README/LICENSE
"""
JL_COMMIT = "adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a"

UNBRICKER = {
    "fm1_unbrick.py": "24c313181927199bb6abcbdb4fe5f20815a32aa1fe0ce85ccc5c2b0217dd613a",
    "fm1_transporter_recover.py": "cd09c78c1acc6b03747d2890734443a76ad3618c9d29ebb6d285334750568a4f",
}

JL = {
    "data/chips.yaml": "6e1dba69c85c72ff18a889281e158f10b1ff959bbff20895b29e268e1e382d24",
    "data/loaderblobs/uart/bd19loader.uart": "fec6acb82c2fd2733691118621c9a26141b73355ee3e187431f3311e88420216",
    "data/loaderblobs/uart/br23loader.uart": "f69fe0c81c94dbb019b806958ac6b3fd7ab2cfc1936e1bce0af947b4f314f495",
    "data/loaderblobs/uart/br25loader.uart": "8959fa9547127d515a5726d66021beecfbc657c6eb3998204523a7af88833289",
    "data/loaderblobs/uart/br30loader.uart": "d86a6195315f9bc103fc36ced99aa8b0be01e18c96a9f4d03ddb07a7668c7e1e",
    "data/loaderblobs/uart/br34loader.uart": "4ea0b8400e6aef9b5ef4ebc9b36c74e91b19ad0ea6fde238a3e13e0f48c7253f",
    "data/loaderblobs/uart/sh54loader.uart": "6bff07f238ed012071cef15040cbf840d7d92a2b5e3735a1613d1a21301ec360",
    "data/loaderblobs/uart/sh55loader.uart": "dc904de58653221c0dc7dc15e8648762aa1637b1471c5a7f268c5f3e1ae4b29d",
    "data/loaderblobs/uart/uc03loader.uart": "ee06554b1a3cc41c8ebe0d8221dc3f79cf41a733a54a51278cc9fc00bf14a712",
    "data/loaderblobs/usb/ac4100loader.bin": "f59faa66543b06894fabc29bb2c4587c882ae97b939f7678d49a36d76acbb1a6",
    "data/loaderblobs/usb/bc51loader.bin": "a7f76bf02dcc4fcbd6cfa65fe68300891bef5a00ce0e9b1b972f7a6d060b1165",
    "data/loaderblobs/usb/bd19loader.bin": "faa1b519a3ff1b082f11ccafac8c73d31150161ac972ef9f1934002bcbb8df4f",
    "data/loaderblobs/usb/bd29loader.bin": "cacf9ffdd37505bf72a6d2a1e20fd6fcd418fbd0ba4cbedd52a1f550b98034ac",
    "data/loaderblobs/usb/br17loader.bin": "74a236822ee2d62198b50fcbfcef09a6816f88ce074a5d46cb37c3f4699d3cff",
    "data/loaderblobs/usb/br20loader.bin": "1ee65cd8062bb5ed74abc3e29089e39cef2a4bf694565d6035ef39b7718409de",
    "data/loaderblobs/usb/br21loader.bin": "9be0879dba0f29c43f73651b38610ae9df8489d1f7dbccf48aa2b9c79b537134",
    "data/loaderblobs/usb/br22loader.bin": "2fc252c7a6aaa27481beb413b34fb57de153fcaf330d3241d7b04b2092b6820d",
    "data/loaderblobs/usb/br23loader.bin": "7384429e33ee1f340fbffe46aa928a0099c163e1374a40f6f9f27468a6f16ddf",
    "data/loaderblobs/usb/br25loader.bin": "0bc69023d6bf4c4d5d5bc048e06249ee39c484acd11196ffc73f88940787d77d",
    "data/loaderblobs/usb/br28loader.bin": "ab1b77eb73385aaa78ab769253b2af7ef63d0ade46f391bd7424cc598a40267c",
    "data/loaderblobs/usb/br30loader.bin": "6e86eff7e0d05fcc8fb00ac80581de05da0ca32575808941414e4201bcfbafbf",
    "data/loaderblobs/usb/br34loader.bin": "c0358b997e82924bc18b80a9c78d2d5fd3f04a48a7aa094a9c7c79d710b13a62",
    "data/loaderblobs/usb/bt15loader.bin": "9f223a7c0e59969f317bf8fd217c414dcd9cb5c66b945cf8bc05a028a9632223",
    "data/loaderblobs/usb/dv12loader.bin": "9f72eb5e20f0882cd48584507d8b473aced9989fc3cc5e15ffb0fdf286067616",
    "data/loaderblobs/usb/dv15loader.bin": "491135e37c8191170bdd04569365394763c79af36b090940ab6de2013c42df92",
    "data/loaderblobs/usb/dv15loader.enc": "f58436ba0f1dc0acd8a621acc82d441394d1592c1c349662a4e2587629550f67",
    "data/loaderblobs/usb/dv16loader.bin": "e008cfb5c81c837e961bc60f9a30222d7f9baef34473930a32dbf5f059cd4b43",
    "data/loaderblobs/usb/sh54loader.bin": "594f030a959af385103aecd442526f8f5177258bee6cfb4f98ef0998895f4edc",
    "data/loaderblobs/usb/uc03loader.bin": "2abcc63e00de8196ae17d689846e346c58fd4d532c8419a2a335736370b3ff24",
    "data/loaderblobs/usb/wl80loader.bin": "701c266e3aaeb882662b0e45a1f626f7888732f93b479b7d5a4363a24dcae89d",
    "data/loaderblobs/usb/wl82loader.bin": "d41da6126760c9d66660bcc0cac8d27d221806c5e369a8036921efe68dca5376",
    "data/uart-loaders.yaml": "06f98c8ab164a9b64a60ecc2a61fe98ad1a942adb7ebe91ccf9c05d922a387b8",
    "data/usb-loaders.yaml": "2c806452cca5cc953ac8e8244c62da7f8c07648f56aa0ab943d62366571f5631",
    "jldevfind.py": "f9a6738e39d40f05df47ef575758e8cf8cc467e7a30b05716b040499e3c9d77e",
    "jlrunner.py": "c6383f1092df2a2bd74d35cf8f927ad1716c021e1768d7dfcb54b8b7f06dc548",
    "jltech/cipher.py": "7a25b514866a2e563818bb91469b030d3d47f8ad0d944b1d5a2c5d9e1ae810b3",
    "jltech/crc.py": "d400960a1a80ef993fc0108610173214c78ae4a9708d137ca6c9eb87c58d6a44",
    "jltech/uboot.py": "d82cfe46e65b1d632de6f29cb48918524bef1644fa8faae6d89fba48193eb789",
    "jltech/utils.py": "f49e770b622768b7d26d30f2ff7808bb9f0a4ad1bfd1b9c0b8033fb3224af0b9",
    "jluboottool.py": "2faec397d717d8143d77b9fe9e0059b32958e6b0fb6cf81949d26539ec476488",
    "requirements.txt": "d67f6908839964dcd0e0b3808c3008f9811fd760f583495cb02460882bb7caa6",
    "scsiio/__init__.py": "b4a753733386f5347f95b422625b3ab377d3dfb33e007059cbcf71d5df65a837",
    "scsiio/common.py": "169846e27387011097e628f74ee26a268854026fbc81675becac1762839b6603",
    "scsiio/linuxport.py": "caff4cd2ecd92f75d07aae3e873b02180f5f49791fbafa05cf7bdc734bb805da",
    "scsiio/win32port.py": "a00d0530f03edd1beba9e08d6786c8e87437d0d7821d731b7d6e54be5a1860ee",
}

# „Reste aufräumen“ (flash/restzone.py) mit seinem Flash-Modell und den Steckbriefen – laufen als root neben dem
# Unbricker. Nach jeder Änderung an einer dieser Dateien neu erzeugen (tests/test_safety.py prüft das):
#   python3 -c "from fm1companion.flash import uboot_manifest as M; print(M.own_lines())"
# 2026-11 (Sicherheitsbefunde S1–S3, docs/RESEARCH-2026-11.md): flashmodel.py neu gepinnt – app_directory /
# directory_layout (Dateitabelle des App-Bereichs mit Kopf- und Datei-CRCs), FACTORY_APPS (Werks-Abbild 8c1d13a5…) und
# check_dump nennt Werks-Abbilder mit gültiger Struktur nicht mehr „beschädigt“. rescuegate.py neu: prüft vor
# restore/repair-settings die Sicherung desselben Laufs (V16-Bootkopf, V16-Aufbau) und läuft dazu als root mit.
OWN = {
    "restzone.py": "3617d3b6489f52135f5e734204b3b4214bf3c0bce2f69c6c1c1961e4a535dcbd",
    "flashmodel.py": "2ab186514ae62f90bae4fc001b040871854075400106cd94a6a6c0eacc9cf4f9",
    "known_packages.json": "3d1e9f45cd2374e4114e4e557583d145ce6de7dde07b6a3feddea637979f0f99",
    "rescuegate.py": "6ae1f5a36e8774906f41066f23155c87cefa9788860a9435dc184205bf9868d8",
}


def own_lines() -> str:
    """die OWN-Zeilen für die aktuellen Dateien (Hilfe beim Neu-Erzeugen)"""
    import hashlib
    from .uboot import OWN_FILES
    return "\n".join(f'    "{n}": "{hashlib.sha256(p.read_bytes()).hexdigest()}",' for n, p in OWN_FILES.items())
