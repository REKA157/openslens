# OpsLens

Backend d'intelligence opérationnelle pour groupes WhatsApp pro.
Pilote : entreprise ADS, groupe "ADS Multi Sites" (Île-de-France).

## Stack

- **Python 3.12 + FastAPI** — webhook + backfill + endpoints internes
- **Supabase** (Postgres + Storage) — base de données et stockage des médias
- **WAHA** (`devlikeapro/waha`) — passerelle WhatsApp Web auto-hébergée
- **Coolify** (sur VPS Hostinger) — orchestration Docker des services

## Architecture phase 1

```
WAHA  ──webhook──►  FastAPI  ──►  Supabase Postgres
                       │
                       └───►  Supabase Storage (médias)
```

Filtre dur sur `PILOT_GROUP_ID` — tout autre chat est ignoré à l'entrée.

## Structure du repo

```
.
├── app/
│   ├── main.py             # entrée FastAPI
│   ├── config.py           # settings env vars
│   ├── db.py               # client Supabase
│   ├── waha.py             # client HTTP WAHA
│   ├── routes/
│   │   ├── health.py       # GET /health
│   │   ├── webhook.py      # POST /ingest/webhook/waha
│   │   └── backfill.py     # POST /admin/backfill
│   └── services/
│       ├── ingest.py       # normalisation + persistance
│       ├── media.py        # téléchargement + upload Storage
│       └── groups.py       # résolution UUID groupe pilote
├── sql/
│   └── bootstrap.sql       # rectifications post-schéma
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md
```

## Variables d'environnement requises

Copie `.env.example` en `.env` et remplis. Ne committe JAMAIS le `.env`.

| Variable | Description |
|---|---|
| `SUPABASE_URL` | URL du projet Supabase |
| `SUPABASE_SECRET_KEY` | Clé secrète (service_role) — bypass RLS |
| `WAHA_BASE_URL` | URL publique du service WAHA (sslip.io) |
| `WAHA_API_KEY` | Clé API WAHA |
| `WAHA_SESSION_NAME` | Nom de la session WAHA (`default`) |
| `WAHA_WEBHOOK_SECRET` | Secret partagé pour valider les webhooks (optionnel) |
| `PILOT_GROUP_ID` | `120363142540472721@g.us` (ADS Multi Sites) |
| `COMPANY_ID` | UUID de la company ADS dans la table `companies` |
| `LOG_LEVEL` | `INFO` par défaut |

## Mise en route locale

```bash
python -m venv .venv
.venv\Scripts\activate              # Windows PowerShell
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API dispo sur `http://localhost:8000`. Docs Swagger sur `/docs`.

## Déploiement

Conteneur Docker → Coolify → même VPS que WAHA.

```bash
# Build local pour tester
docker build -t opslens-backend .
docker run -p 8000:8000 --env-file .env opslens-backend
```

## Endpoints

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/health` | Healthcheck |
| GET | `/` | Info service |
| POST | `/ingest/webhook/waha` | Réception événements WAHA |
| POST | `/admin/backfill` | Charger l'historique du groupe |
