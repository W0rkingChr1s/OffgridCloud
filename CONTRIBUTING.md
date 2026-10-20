# Beiträge zu OffgridCloud

Danke fürs Mithelfen! Dieses Dokument beschreibt das lokale Setup und die
Konventionen.

## Repo-Struktur

```
backend/    FastAPI-App (API + liefert das statische UI aus + steuert rclone)
frontend/   React + Vite + TypeScript + Tailwind (zu statischen Dateien gebaut)
deploy/     Dockerfile, systemd-Unit, native Install-Skripte
docs/        Konzept, Betriebshandbuch & Entwicklungsplan
assets/      Logo & Brand-Assets
```

## Backend (Python 3.11+)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .          # Lint
pytest -q             # Tests
uvicorn app.main:app --reload   # http://localhost:8000
```

## Frontend (Node 20+)

```bash
cd frontend
npm install
npm run dev           # http://localhost:5173 (proxyt /api nach :8000)
npm run build         # Produktions-Build nach dist/
```

Für einen produktionsnahen Lauf das Frontend bauen und `frontend/dist` nach
`backend/app/static` kopieren; FastAPI liefert das UI dann unter `/` als ein
einziger Prozess aus.

## Konventionen

- **Backend:** ruff-sauber, Type-Hints, kleine fokussierte Module.
- **Frontend:** TypeScript strict mode, Tailwind fürs Styling, Brand-Palette
  (`ogc-teal` / `ogc-blue` / `ogc-indigo`, definiert als `@theme` in
  `frontend/src/index.css`).
- **Commits:** klare Nachrichten im Imperativ.
- **Raspberry Pi 3 zuerst:** die Laufzeit auf einen Prozess beschränken;
  keine Dauer-Dienste (Redis o. Ä.) ohne triftigen Grund. Nie ganze Mediendateien
  im RAM puffern — auf Platte streamen.

## Auto-Merge

PRs mit dem Label **`automerge`** merged `.github/workflows/automerge.yml`
selbstständig (Squash, Branch wird gelöscht), sobald alle Checks auf dem
Head-Commit grün sind. Pflicht ist dabei „Docker image (build only)“, also
Backend, Frontend und Image-Build. Dependabot-PRs für Patch- und
Minor-Updates bekommen das Label automatisch, Major-Updates bleiben Handarbeit.
Abbrechen geht über das Entfernen des Labels oder den Draft-Status.

Ein Merge mit dem Standard-`GITHUB_TOKEN` löst keine weiteren Workflows aus.
Wer CI auch auf `main` nach dem Merge will, hinterlegt einen fine-grained PAT
(Contents + Pull requests: write) als Secret `AUTOMERGE_TOKEN`, für
Dependabot-PRs zusätzlich als Dependabot-Secret.

## Roadmap

Siehe [docs/ENTWICKLUNGSPLAN.md](docs/ENTWICKLUNGSPLAN.md).
