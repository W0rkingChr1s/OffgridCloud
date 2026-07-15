# scripts/

Hilfsskripte für die Projektpflege.

## `sync_wiki.py` — Wiki aus `docs/` erzeugen

Die Seiten im **[Projekt-Wiki](https://github.com/W0rkingChr1s/OffgridCloud/wiki)**
sind ein Spiegel des Ordners [`docs/`](../docs). Dieses Skript regeneriert sie:
es überträgt jede `docs/*.md` auf ihre Wiki-Seite, schreibt die internen Links in
die Wiki-Schreibweise um (`[docs/X.md](X.md)` → `[Seitenname](Seitenname)`) und
erzeugt die Index-Seiten `Home` und `_Sidebar` aus derselben Seitentabelle — neue
Dokumente tauchen so überall gleichzeitig auf.

Das Wiki ist ein **eigenes Git-Repository** (`<repo>.wiki.git`). Das Skript muss
daher dort laufen, wo Push-Zugriff darauf besteht (ein normaler Checkout dieses
Repos auf einer Maschine mit GitHub-Zugang genügt). Nur Python-Standardbibliothek,
keine Abhängigkeiten.

```bash
# Vorschau: klont das Wiki in ein Temp-Verzeichnis, regeneriert, zeigt den Diff,
# pusht NICHT:
python3 scripts/sync_wiki.py --dry-run

# Regenerieren + committen + pushen (Standard):
python3 scripts/sync_wiki.py

# Optionen:
python3 scripts/sync_wiki.py --wiki-url <URL>   # Wiki-Remote überschreiben
python3 scripts/sync_wiki.py --wiki-dir <DIR>   # vorhandenen Wiki-Checkout nutzen
python3 scripts/sync_wiki.py --message "…"      # Commit-Nachricht
```

Standardmäßig wird die Wiki-URL aus dem `origin`-Remote abgeleitet
(`<origin ohne .git>.wiki.git`).

**Neues Dokument aufnehmen:** in `docs/` anlegen und einen Eintrag in der
`PAGES`-Tabelle in `sync_wiki.py` ergänzen (docs-Datei, Wiki-Seitenname, Anzeige-
Label, Icon, Kurzbeschreibung), dann das Skript ausführen.
