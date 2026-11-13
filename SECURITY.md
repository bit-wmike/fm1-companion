# Security

## Reporting a vulnerability

Please do **not** report security vulnerabilities as a public issue, but privately via GitHub:
tab **Security** › **Report a vulnerability** in the repository
(<https://github.com/bit-wmike/fm1-companion/security/advisories/new>).

Helpful: affected version (`fm1-companion --version`), steps to reproduce, what happens
and what you expected. Please do not send tokens, passwords or personal data – not even
in attached error reports (the app removes tokens, but check the content beforehand anyway).

The project is maintained in spare time; we will get back to you as soon as possible and publish a
fix together with an advisory once it is ready.

## Scope

* Code in this repository: the app, the installers (`install.sh`, `.run`, `.deb`), the tools in `tools/`.
* Anything that unintentionally discloses data (error report, diagnostics, logs), runs code from outside
  or changes the FM-1 without consent.
* **Device safety is explicitly welcome:** ways in which a web page, a simulator, a
  package or an operating mistake could brick an FM-1, even if it is "only" a bug.

Out of scope are vulnerabilities in the firmwares themselves (Melodee, Skiff, SLOOP, original firmware) or in
third-party tools – please report those directly to their projects. Vendored tools (e.g.
`fm1companion/vendor/unbricker`) can be reported here; we will pass it on.

## Known limitations

* **Root helper of rescue level 2** (`fm1companion/flash/uboot.py`): Before starting with root privileges,
  the tools are copied into a root-owned temporary directory and checked against built-in SHA-256 checksums.
  These checksums ensure **integrity** (no damaged or accidentally modified files); they are **not a security
  boundary** against malicious code that already runs with your user privileges – anything that can do that can
  also modify the app itself. The tool writes its backups and logs as root into the rescue folder under
  `~/FM-1/Rettung`; afterwards the helper hands the files over to your user without following symbolic links.
* **What web pages may send to the FM-1.** Online editors and simulators are third-party web pages. The app
  checks every single MIDI message a page sends, against the address the page has at that moment:
  * *Every page, without asking* (online simulators, typed addresses, pages you did not allow): single normal
    MIDI messages only – notes, controllers, program change, pitch bend, clock/start/stop. No SysEx at all.
  * *Simulators bundled with the app* (served from the app's own local address): additionally the FM1 Open
    library protocol (`F0 7D 46 4D …`), which can read, overwrite and delete projects, sounds and the sample
    bank of an FM-1 that runs FM1 Open.
  * *“FM-1 Workbench (Community)” (online), after you allowed its address:* the same FM1 Open library protocol,
    nothing more.
  * *Online editors (catalog link “Online editor”), after you allowed the address:* SysEx of the editor family
    (`F0 7D …`, this includes the FM1 Open library) and universal SysEx (`F0 7E …`, `F0 7F …`). With these a
    page can read, change and delete sounds, presets, projects, samples and settings and restore backups.
  * *The Editor view* (the web editor from the firmware's own repository, served locally by the app): everything
    the editor sends, **except** SysEx starting with `F0 22 24 35` (the update-mode key `… 7F` and the boot-mode
    key `… 7D`). None of the editors we know (Melodee, Skiff, SLOOP, Felucca, KARMA, ChoralRoot) sends these
    keys from its editor page; their separate web installers do, and in the app all installing goes through
    “Firmware+”.
  * *Never, from any web page:* the update-mode and boot-mode keys, the M-VAVE update data stream, and any
    SysEx not listed above.

  Permissions are asked once per address (origin), can only be given to `https://` addresses or pages on this
  computer, and stop applying the moment the page moves to another address. The app cannot check what an
  allowed page does with its permission.
* **Downloads from web pages** are saved only after the normal “Save” dialog. The one exception is the WAV file
  the app itself requests from a bundled simulator when you stop a recording; it never replaces an existing file.
* **Bug reports** are created only when you ask for one and are never sent automatically. Credentials, the home
  folder, user and computer name, device names, notes, serial numbers (except the last four characters), MAC
  addresses and Wi-Fi names are removed by pattern matching. A report can still contain file and folder names,
  names of USB devices, network addresses and other details – look through it before you post it publicly.
* The local web server of the app listens on 127.0.0.1 only. Other programs running on the same computer can
  read the files it serves (editor copies, simulators). What you play on the FM-1 is relayed only to the
  address with the random key that the app puts into the simulator link it opens in Firefox; other programs of
  the same user can see that link while Firefox runs.
* A firmware's resource tool is Python code from its repository. For built-in firmwares the commit is pinned per
  version; for firmwares you added yourself and for moved tags the app asks before running it.
