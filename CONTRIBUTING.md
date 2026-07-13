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
  (`ogc.teal` / `ogc.blue` / `ogc.indigo`).
- **Commits:** klare Nachrichten im Imperativ.
- **Raspberry Pi 3 zuerst:** die Laufzeit auf einen Prozess beschränken;
  keine Dauer-Dienste (Redis o. Ä.) ohne triftigen Grund. Nie ganze Mediendateien
  im RAM puffern — auf Platte streamen.

## Roadmap

Siehe [docs/ENTWICKLUNGSPLAN.md](docs/ENTWICKLUNGSPLAN.md).
