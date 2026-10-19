# Third-party notices

The MIT license in `LICENSE` applies to this project's original code. Third-party packages and bundled runtimes retain their respective licenses. This document is an overview, not a substitute for their license texts.

## Runtime and build components

- **Python** — Python Software Foundation License and included notices. The Windows executable package embeds a Python runtime.
- **Tcl/Tk** — their own permissive licenses. Tkinter uses Tcl/Tk for the desktop interface.
- **Selenium** — Apache License 2.0. Used to control the user's Chrome browser. Selenium Manager may fetch a compatible driver on first use.
- **PyInstaller** — GPL with the PyInstaller bootloader exception. A build tool; its exception permits distribution of generated applications under their own license. Consult the bundled license for exact terms.
- **Other Python dependencies** — licenses belong to their respective authors. Direct dependency constraints are listed in `requirements.txt`; the Windows package records actual bundled versions in `third_party_licenses/versions.json` and includes their license texts.

Chrome and ChromeDriver are Google/Chromium components with their own terms. Chrome must be installed separately. A downloaded driver is not an original component of this project; no driver binary from the reference repositories is included.

When distributing a Windows build, retain the generated third-party license directory and notices alongside the application. A source checkout contains dependency requirements rather than third-party package source.

## README artwork

The project logo and badminton banner are original vector artwork under MIT. The campus photograph has been removed from the current branch. Historical releases retain the notices distributed with those releases. See [asset notes](docs/assets/ATTRIBUTION.md).

## Source-review references

Development included reviewing these public projects:

- <https://github.com/Jian2032/neu_badminton_tool> — reviewed revision `a13ee6b894fc3311c8121f510f75f89423fd8532`.
- <https://github.com/MeowgicalAI/Auto-Reservation>.

Their complete redistribution licenses were not verified. They are references for understanding the public school-page workflow, not components licensed or redistributed by this project. This release does not include their source files, bundled browser drivers, executables, or personal configuration. Shared school element identifiers and URLs describe the external school's website.

No independent evidence establishes a reliable high-traffic reservation success rate for either reference project or for this application. References are not endorsements or warranties.
