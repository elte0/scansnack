---
status: accepted
date: 2026-10-30
---

# English as the only repository language

## Context and problem statement

The protocol docs, the website and the changelog are in English (so that the protocol knowledge is
useful to anyone), but code comments, docstrings, CLI and log messages, test descriptions, scripts and
workflow step names were in German, as ADR 0003 had set for code comments. The project is public, and
readers of the protocol docs follow links into the code (`# UNVERIFIED:` markers, packet layouts,
sequences); a second language there is a barrier.

## Decision drivers

* Anyone who reads the protocol docs can also read the code that implements them.
* One language for everything committed, so that contributors never have to choose.
* No change in behavior: protocol data, captures and fixtures stay byte for byte as they are.

## Considered options

* keep code comments in German (ADR 0003)
* English for everything in the repository

## Decision outcome

Chosen: **English for everything in the repository**: code comments, docstrings, CLI help and
messages, log output, error texts, test names and comments, scripts in `tools/`, `packaging/` and `ios/`,
workflow and step names, `CLAUDE.md`, and commit messages from now on. This supersedes the sentence
"code comments in German" in ADR 0003; the rest of ADR 0003 stays in force.

Exceptions: quotations of what a device shows or sends (for example the text of a German-language
touch panel in `docs/protocol.md`) stay verbatim, and earlier commit messages are not rewritten.

### Consequences

* Good: the code is readable for everyone who reads the protocol docs.
* Neutral: CLI and log messages change wording; nothing parses them.
