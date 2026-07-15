#!/usr/bin/env python3
"""Regenerate the GitHub wiki from the repo's ``docs/`` folder and push it.

The wiki pages are a mirror of ``docs/``: each ``docs/*.md`` maps to a wiki page,
and intra-doc links (``[docs/X.md](X.md)`` / ``](X.md#anchor)``) are rewritten to
wiki-link form (``[Friendly](Friendly)`` / ``](Friendly#anchor)``). The wiki-only
index pages (``Home`` and ``_Sidebar``) are generated from the same page table so
new documents show up everywhere at once.

The wiki lives in a *separate* Git repository (``<repo>.wiki.git``), so this must
run somewhere with push access to it — a plain checkout of this repo on a machine
that can reach GitHub is enough.

Usage::

    python3 scripts/sync_wiki.py                 # clone wiki, regenerate, commit, push
    python3 scripts/sync_wiki.py --dry-run       # regenerate into a temp clone, show diff, no push
    python3 scripts/sync_wiki.py --wiki-url URL  # override the wiki remote (default: origin + .wiki.git)
    python3 scripts/sync_wiki.py --wiki-dir DIR  # use/keep an existing wiki checkout at DIR

Nothing here needs third-party packages — standard library only.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"

# docs filename -> wiki page. ``page`` is the wiki filename stem and link target
# (file is "<page>.md"); ``label`` is the human display text in the index pages.
# They match except where the title reads better with a space (OffgridCloud OS).
# Order here drives the Home/Sidebar order.
PAGES: list[tuple[str, str, str, str, str]] = [
    # (docs file,               wiki page,            label,                icon, one-line blurb)
    ("BETRIEB.md",            "Betriebshandbuch",   "Betriebshandbuch",   "📗", "Installation, Absicherung, Betrieb, Troubleshooting"),
    ("KONZEPT.md",            "Konzept",            "Konzept",            "📘", "Vision, Architektur, Datenmodell, Tech-Stack"),
    ("KIOSK.md",              "OffgridCloud-OS",    "OffgridCloud OS",    "🖥️", "Vollbild-Menü an der Box (Konsole/Kiosk) mit PIN-geschütztem OS-Zugang"),
    ("NETZWERK-REDUNDANZ.md", "Netzwerk-Redundanz", "Netzwerk-Redundanz", "📡", "WLAN-Rückfallebene bei Router-Ausfall"),
    ("VPN.md",                "VPN-Client",         "VPN-Client",         "🔐", "Ins Heimnetz einwählen (WireGuard/OpenVPN)"),
    ("MULTI-SERVER-POOL.md",  "Multi-Server-Pool",  "Multi-Server-Pool",  "🛰️", "Mehrere Boxen als Flotte"),
    ("CLIENT.md",             "Desktop-Client",     "Desktop-Client",     "💻", "Auto-Upload-Agent für macOS/Linux/Windows (Plan)"),
    ("ENTWICKLUNGSPLAN.md",   "Entwicklungsplan",   "Entwicklungsplan",   "🗺️", "Roadmap in Phasen & Meilensteinen"),
]
DOC_TO_PAGE = {doc: page for doc, page, _, _, _ in PAGES}

REPO_URL = "https://github.com/W0rkingChr1s/OffgridCloud"

_LINK = re.compile(r"\[([^\]]*)\]\((?:docs/)?([A-Z0-9-]+\.md)(#[^)]*)?\)")


def _rewrite_links(body: str) -> str:
    def repl(m: re.Match) -> str:
        text, target, anchor = m.group(1), m.group(2), m.group(3) or ""
        page = DOC_TO_PAGE.get(target)
        if not page:
            return m.group(0)  # link to a doc we don't publish — leave untouched
        if text in (target, f"docs/{target}"):
            text = page  # display text was the bare path → use the friendly name
        return f"[{text}]({page}{anchor})"

    return _LINK.sub(repl, body)


def _sidebar() -> str:
    lines = ["### OffgridCloud", "", "- **[Home](Home)**"]
    for _doc, page, label, icon, _blurb in PAGES:
        lines.append(f"- {icon} [{label}]({page})")
    lines += ["", "---", f"[⟵ Repository]({REPO_URL})", ""]
    return "\n".join(lines)


def _home() -> str:
    rows = "\n".join(
        f"| {icon} **[{label}]({page})** | {blurb} |"
        for _doc, page, label, icon, blurb in PAGES
    )
    return f"""# OffgridCloud Wiki

Ein selbst-gehosteter Mini-Server, der Medien-Uploads aus dem Feld von instabilen
Verbindungen **entkoppelt** und sie zuverlässig in Public-Cloud-Speicher überträgt —
**sobald ausreichend Bandbreite vorhanden ist.** Sparsam genug für einen **Raspberry Pi 3**.

> Projekt-Repository: [W0rkingChr1s/OffgridCloud]({REPO_URL})

## Dokumentation

| Seite | Inhalt |
|---|---|
{rows}

## Schnelleinstieg

Der schnellste Weg zu einer laufenden Box steht im **[Betriebshandbuch](Betriebshandbuch)**.
Ein Ein-Zeilen-Installer und die Docker-Variante sind in der
[README]({REPO_URL}#installation) beschrieben.

---

_Diese Wiki-Seiten werden aus dem Ordner [`docs/`]({REPO_URL}/tree/main/docs)
im Repository gepflegt (siehe `scripts/sync_wiki.py`)._
"""


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


def _default_wiki_url() -> str:
    origin = _git(["remote", "get-url", "origin"], REPO_ROOT)
    return re.sub(r"\.git$", "", origin) + ".wiki.git"


def regenerate(wiki_dir: Path) -> list[str]:
    """Write all wiki pages into ``wiki_dir``; return a list of changed page names."""
    changed: list[str] = []
    outputs = {f"{page}.md": _rewrite_links((DOCS / doc).read_text("utf-8"))
               for doc, page, _, _, _ in PAGES}
    outputs["_Sidebar.md"] = _sidebar()
    outputs["Home.md"] = _home()
    for name, content in outputs.items():
        dest = wiki_dir / name
        old = dest.read_text("utf-8") if dest.exists() else None
        if old != content:
            dest.write_text(content, "utf-8")
            changed.append(name)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync the GitHub wiki from docs/.")
    ap.add_argument("--wiki-url", default=None, help="wiki remote (default: origin + .wiki.git)")
    ap.add_argument("--wiki-dir", default=None, help="existing wiki checkout to use (default: temp clone)")
    ap.add_argument("--dry-run", action="store_true", help="regenerate and show diff, do not commit/push")
    ap.add_argument("--message", default="Sync wiki from docs/", help="commit message")
    args = ap.parse_args()

    tmp: tempfile.TemporaryDirectory | None = None
    if args.wiki_dir:
        wiki_dir = Path(args.wiki_dir).resolve()
        if not (wiki_dir / ".git").is_dir():
            print(f"error: {wiki_dir} is not a git checkout", file=sys.stderr)
            return 2
    else:
        url = args.wiki_url or _default_wiki_url()
        tmp = tempfile.TemporaryDirectory(prefix="ogc-wiki-")
        wiki_dir = Path(tmp.name) / "wiki"
        print(f"Cloning wiki: {url}")
        subprocess.run(["git", "clone", url, str(wiki_dir)], check=True)

    changed = regenerate(wiki_dir)
    if not changed:
        print("Wiki already up to date — nothing to do.")
        return 0
    print("Changed pages:\n  " + "\n  ".join(sorted(changed)))

    if args.dry_run:
        subprocess.run(["git", "add", "-A"], cwd=wiki_dir, check=True)
        subprocess.run(["git", "--no-pager", "diff", "--cached", "--stat"], cwd=wiki_dir, check=False)
        print("\n(dry run — not committing or pushing)")
        return 0

    subprocess.run(["git", "add", "-A"], cwd=wiki_dir, check=True)
    subprocess.run(["git", "commit", "-m", args.message], cwd=wiki_dir, check=True)
    subprocess.run(["git", "push", "origin", "HEAD"], cwd=wiki_dir, check=True)
    print("Wiki pushed.")
    if tmp:
        tmp.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
