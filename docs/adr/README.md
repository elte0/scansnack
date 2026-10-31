# Architecture Decision Records

Architecture and protocol decisions of the project, in [MADR](https://adr.github.io/madr/) format.
File name `NNNN-title.md`, numbered consecutively.

Existing ADRs are never silently overridden: if a decision is to change, a new ADR records the new
decision and sets the old one to "superseded by NNNN".

| No. | Title | Status |
|---|---|---|
| [0001](0001-escl-bridge-instead-of-macos-driver.md) | eSCL bridge instead of a native macOS driver | accepted |
| [0002](0002-hexagonal-architecture.md) | Hexagonal architecture with enforced import rules | accepted |
| [0003](0003-desktop-app-with-pyside6.md) | Desktop app in Python with PySide6 | accepted (comment language superseded by 0006) |
| [0004](0004-ios-app-with-beeware.md) | iOS app with BeeWare/Toga instead of PySide6 | accepted |
| [0005](0005-own-updater-on-github-releases.md) | Own small updater on top of GitHub Releases | accepted |
| [0006](0006-english-as-repository-language.md) | English as the only repository language | accepted |
| [0007](0007-ix500-beta-in-the-wifi-adapter.md) | iX500 as a beta model in the Wi-Fi adapter, with an explicit model choice | accepted |
