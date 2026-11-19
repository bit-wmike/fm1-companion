# Bundled tools

Files the FM-1 Companion Suite ships unchanged from other projects, pinned in `../manifest.json` (version, release
tag and commit, URL, size, SHA-256, licence). The app checks the SHA-256 of every file before it uses it.

| Folder | Project | Licence | What is here |
| --- | --- | --- | --- |
| `m-flasher/` | [M-Flasher](https://github.com/jvitkauskas/m-flasher) 0.12.1 | MIT (fonts OFL-1.1) | licence, notices, upstream udev rule, crate list. **The binaries are not in git**: `tools/fetch_tools.py` downloads the two release tarballs (aarch64, x86_64) at build time, checks them against the manifest and puts the binaries into `tools-bin/` of the packages |
| `m-bootloader/` | [m-bootloader](https://github.com/jvitkauskas/m-bootloader) 2.4 | MIT | `fm1-uboot.boot` (checked against the release's `SHA256SUMS`, included), licence, notice |
| `sloop3/` | [SLOOP 3](https://github.com/isod89/sloop3-fm1) 3.4.0 (public beta) | GPL-3.0 | `sloop3-3.4.0.area`, licence and notices; source: see `sloop3/NOTICE` |
