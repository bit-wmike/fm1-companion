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
* Online editors and simulators are third-party web pages. They may send editor commands to the FM-1 only after
  your consent per address; firmware update and boot mode always stay blocked.
* A firmware's resource tool is Python code from its repository. For built-in firmwares the commit is pinned per
  version; for firmwares you added yourself and for moved tags the app asks before running it.
