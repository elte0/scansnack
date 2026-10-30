# Documentation site

A minimal [Hugo](https://gohugo.io/) site, "ScanSnack", that presents the app and publishes the protocol
write-up on GitHub Pages.

> **Summary.** Apart from the home page and the app page (`content/app.md`) the site has no content of
> its own: it mounts `docs/protocol.md`, `docs/airscan.md`, `docs/disclaimer.md` and `CHANGELOG.md` (see `hugo.toml`), so
> those files stay the single source. Screenshots and download links of the app page are generated at
> build time, so they follow the code and the latest release. No theme, no external dependencies.

## Contents

- [Build locally](#build-locally)
- [Deployment](#deployment)
- [App page](#app-page)
- [Layout](#layout)

## Build locally

Requires Hugo ≥ 0.123 (the workflow pins 0.147.9).

```sh
uv run --extra app python tools/screenshots.py   # optional: app screenshots into site/static/screenshots/
hugo server --source site                        # preview at http://localhost:1313/scansnack/
hugo --source site                               # build into site/public/
```

Without the screenshots the app page simply leaves the images out. Without `data/release.json` it shows
a link to the releases page instead of download buttons (see [App page](#app-page)).

## Deployment

`.github/workflows/pages.yml` builds the site and deploys it to GitHub Pages on every push to `main`
that touches the site or the mounted docs, or when started manually. Pull requests that touch them only
build the site, so a broken layout or mount shows up before the merge.
In the repository settings, Pages → Source must be set to "GitHub Actions".
After every release, `.github/workflows/release.yml` starts the Pages workflow on `main` again, so the
download buttons point to the new version within minutes.

## App page

`content/app.md` (layout `layouts/_default/app.html`) holds the text: features and getting started.
Its feature list is the only one in the repository (the README links to it): every change that adds,
changes or removes a user-visible feature updates it in the same commit, next to the `CHANGELOG.md`
entry. Generated during the Pages build, never committed (`.gitignore`):

| File | Source |
|---|---|
| `static/screenshots/{main,connect}-{light,dark}.png` | `tools/screenshots.py`: runs the real `MainWindow` offscreen, scans sample pages through the real scan path (sample scanner instead of the network) and grabs the window in light and dark mode |
| `data/release.json` | GitHub API `releases/latest`; the buttons find their file by the name ending in the front matter (`platforms`, names from `tools/build_app.py`) |

The Pages workflow also runs when the user interface (`src/scansnap/adapters/qt/`, `src/scansnap/core/`)
or the screenshot script changes. The page highlights the buttons for the visitor's operating system.

## Layout

| Path | Purpose |
|---|---|
| `hugo.toml` | configuration and the mounts from `docs/` and `CHANGELOG.md` (page `/changelog/`) |
| `content/_index.md` | home page |
| `content/app.md` | app page: features, getting started, platforms for the download buttons |
| `layouts/` | page templates (single page, home, app page, title from the first heading), partials for screenshots and downloads, shortcode `screenshot` |
| `assets/style.css` | stylesheet (light/dark) |
