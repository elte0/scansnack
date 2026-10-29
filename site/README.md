# Documentation site

A minimal [Hugo](https://gohugo.io/) site that publishes the protocol write-up on GitHub Pages.

> **Summary.** The site has no content of its own apart from the home page: it mounts
> `docs/protocol.md` and `docs/disclaimer.md` (see `hugo.toml`), so `docs/` stays the single source.
> No theme, no external dependencies.

## Contents

- [Build locally](#build-locally)
- [Deployment](#deployment)
- [Layout](#layout)

## Build locally

Requires Hugo ≥ 0.123 (the workflow pins 0.147.9).

```sh
hugo server --source site          # preview at http://localhost:1313/scansnack/
hugo --source site                 # build into site/public/
```

## Deployment

`.github/workflows/pages.yml` builds the site and deploys it to GitHub Pages on every push to `main`
that touches the site or the mounted docs, or when started manually.
In the repository settings, Pages → Source must be set to "GitHub Actions".

## Layout

| Path | Purpose |
|---|---|
| `hugo.toml` | configuration and the mounts from `docs/` |
| `content/_index.md` | home page |
| `layouts/` | page templates (single page, home, title from the first heading) |
| `assets/style.css` | stylesheet (light/dark) |
