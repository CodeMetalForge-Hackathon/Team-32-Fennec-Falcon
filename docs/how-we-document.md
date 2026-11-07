# How we document

This repository is judged on its documentation as much as its code. The rule: **nothing is built without a written record of what it is, why we built it, and how we know it works.**

## Every time we build something

1. **Commit the work** with a message that says what changed and why (format below).
2. **Add a build-log entry** to [build-log.md](build-log.md) using the template there: what, why, how it works, key results, decisions and trade-offs, verification, open issues.
3. **Update the reference doc** for anything whose behaviour changed (for the simulator, [simulator.md](simulator.md)): inputs, equations, constants, sources, limitations.
4. **Add screenshots** to `docs/images/` for anything with a screen, and reference them from the docs.
5. **Label every number** as *measured* or *modelled*, with units and a source.

## Where things go

| Path | Contents |
|---|---|
| `README.md` | What the project is, how to run it, headline results, map of the repo |
| `simulator/` | The Eagle Jungle Simulator and its headless model runner |
| `docs/simulator.md` | Technical reference for the simulator |
| `docs/build-log.md` | Dated record of every build |
| `docs/how-we-document.md` | This file |
| `docs/images/` | Screenshots and diagrams |

New software gets its own top-level folder (for example `tag-firmware/`, `atak-plugin/`) and its own reference doc in `docs/`.

## Commit messages

```
<Area>: <what changed, imperative, under 60 characters>

<Why it changed and anything a reviewer needs to know.>
```

Examples: `Simulator: add terrain masking to RF model` · `Docs: add thermal camera comparison`.

## Before anything is pushed: privacy and open-source checklist

The repository will be public under Apache-2.0. Git keeps every version of every file, so **deleting something later does not remove it from the history**. Check before you commit:

- [ ] No passwords, API keys, tokens or credentials, including in config files and code comments.
- [ ] No personal email addresses or phone numbers, home addresses, or photos of people.
- [ ] No internal company material: pitch decks, financials, pricing, investor or advisor names, contact lists, grant applications.
- [ ] No chat transcripts, AI session links or meeting notes; write up the result instead.
- [ ] No controlled (CUI), export-controlled (ITAR/EAR) or classified information.
- [ ] Nothing the team would not want reused: Apache-2.0 lets anyone use, modify and sell this code, and includes a patent licence from contributors.
- [ ] Images contain only what you mean to show (check for open tabs, notifications, file paths, locations).

## Adding files without the command line

1. On the repository page, click **Add file → Upload files** (or **Create new file**).
2. To put a file in a folder, open that folder first; with **Create new file** you can type `folder/name.md` in the name box to create a new folder.
3. Fill in the commit message (format above), choose **Commit directly to the `main` branch**, then **Commit changes**.
