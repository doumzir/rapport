# WorkTracer — CLAUDE.md

## Contexte du projet

WorkTracer est une API/webapp de suivi de travail multi-projets avec génération automatique de rapports via Claude API.
L'utilisateur gère 3-4 projets sur 3-4 entreprises différentes avec des rôles multiples (dev, infra, SEO, SEA, design).

**Objectif** : capturer automatiquement l'activité (git hooks + saisie manuelle), la structurer en base, et générer des rapports hebdo/mensuel/trimestriel neutres et rigoureux à double audience (technique + exécutif non-tech).

**Hébergement** : Railway (PostgreSQL inclus, free tier)
**Stack** : Python 3.12 · FastAPI · SQLAlchemy 2.x · APScheduler · Claude API · HTMX · Jinja2

---

## Architecture

```
main.py          → FastAPI app, routes, auth, dashboard HTML
models.py        → SQLAlchemy models (Company, Project, WorkEntry, Report)
database.py      → connexion DB, init_db(), get_db()
reports.py       → génération Claude (weekly/monthly/quarterly), calcul des périodes
scheduler.py     → APScheduler, 3 crons automatiques
hooks/git-hook.sh→ script post-commit installable via curl | bash
templates/       → HTML HTMX Jinja2 (base, index, project, report)
```

---

## Conventions de code

### Python
- Python 3.12+, pas de `from __future__ import annotations`
- SQLAlchemy 2.x avec `Mapped` / `mapped_column` — ne pas utiliser l'ancienne API `Column()`
- Pydantic v2 pour les schémas de requête dans `main.py`
- Pas de `async def` pour les routes DB (SQLAlchemy sync) — utiliser `def` + `Depends(get_db)`
- Les dates sont toujours timezone-aware (`datetime.now(timezone.utc)`) — jamais de `datetime.utcnow()`
- Les enums Python héritent de `str, enum.Enum` pour la sérialisation JSON automatique
- Tags stockés en chaîne CSV (`"seo,dev,fix"`) — pas de table séparée, pas d'ORM relation pour ça

### Nommage
- Slugs : kebab-case (`mon-projet-web`) — uniques, utilisés comme identifiants dans l'API
- Endpoints : snake_case pour les query params, kebab-case pour les paths
- Modèles DB : snake_case pour les colonnes, PascalCase pour les classes

### Auth
- Header `X-API-Key` pour tous les endpoints API
- Cookie `api_key` pour le dashboard HTML (login via `POST /login`)
- `require_api_key` = dépendance FastAPI injectable — toujours utiliser `dependencies=[Depends(require_api_key)]`
- Ne jamais logger ou exposer la valeur de `API_KEY`

---

## Modèle de données — règles importantes

```
Company  →  1:N  →  Project  →  1:N  →  WorkEntry
                  Project  →  1:N  →  Report
```

- `Report.project_id` peut être `NULL` = rapport global tous projets
- `WorkEntry.source` : `git` | `manual` | `note` — ne pas ajouter de nouvelles valeurs sans migrer l'enum en DB
- `WorkEntry.metadata_json` : JSON string libre (pas de colonne JSON native pour compatibilité SQLite dev)
- `Project.roles` : CSV libre (`"dev,infra,seo"`) — pas d'enum contrainte
- `Project.status` : `active` | `paused` | `completed` | `archived`

---

## Rapports — règles critiques (ne pas modifier sans raison forte)

### Modèles Claude utilisés
| Type rapport | Modèle | Raison |
|---|---|---|
| Hebdomadaire | `claude-haiku-4-5-20251001` | Volume élevé, coût minimal |
| Mensuel | `claude-haiku-4-5-20251001` | Idem |
| Trimestriel | `claude-sonnet-4-6` | Analyse profonde, 1x/trimestre |

### Prompt system — principe fondateur
Le prompt `SYSTEM_PROMPT` dans `reports.py` est **non négociable** :
- Neutralité absolue, rigueur factuelle, zéro complaisance
- Pas de confort psychologique, pas de blabla motivationnel
- Identification des risques présents ET futurs
- La plus-value se calcule, elle ne se ressent pas
- Rapport neutre ≠ rapport vide : c'est un rapport honnête

Ne jamais adoucir, reformuler positivement, ou atténuer le prompt système.

### Structure de sortie
Chaque rapport contient **2 sections séparées par `---EXECUTIVE---`** :
1. `content_technical` — pour le contributeur, détaillé, factuel
2. `content_executive` — pour des dirigeants non-tech, langage business, indicateurs 🟢/🟡/🔴

---

## Scheduler

- **Hebdomadaire** : lundi 7h Europe/Paris — semaine précédente (lundi→dimanche)
- **Mensuel** : 1er du mois 7h — mois précédent complet
- **Trimestriel** : 1er janvier, avril, juillet, octobre 7h — trimestre précédent

Le scheduler démarre dans le lifespan FastAPI (`start_scheduler()` dans `main.py`).
Ne jamais bloquer le thread du scheduler — les jobs DB utilisent leur propre `SessionLocal()`.

---

## Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `DATABASE_URL` | oui | `postgresql://...` (Railway) ou `sqlite:///./worktracer.db` (dev local) |
| `ANTHROPIC_API_KEY` | oui | Clé API Anthropic |
| `API_KEY` | oui | Secret auth (générer avec `openssl rand -hex 32`) |
| `TZ` | non | Timezone scheduler (défaut : `Europe/Paris`) |

Railway injecte parfois `postgres://` — `database.py` corrige automatiquement en `postgresql://`.

---

## Développement local

```bash
cd worktracer
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # remplir les valeurs
uvicorn main:app --reload --port 8000
```

En local, si `DATABASE_URL` n'est pas défini, SQLite est utilisé (`worktracer.db`).
Le dashboard est accessible sur `http://localhost:8000` (login avec la valeur de `API_KEY`).

---

## Déploiement Railway

1. Push sur GitHub (branche `main`)
2. Railway : New Project → PostgreSQL service + Web service (Dockerfile)
3. Configurer les 3 variables d'env dans Railway
4. `railway up` ou push → déploiement automatique
5. Health check : `GET /health` → `{"status":"ok","db":"connected"}`

---

## Ajout d'un nouveau projet/entreprise (workflow)

```bash
# 1. Créer l'entreprise
curl -X POST https://<host>/companies \
  -H "X-API-Key: <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Corp", "slug": "acme"}'

# 2. Créer le projet
curl -X POST https://<host>/projects \
  -H "X-API-Key: <token>" \
  -H "Content-Type: application/json" \
  -d '{"company_slug":"acme","name":"Site vitrine","slug":"acme-site","roles":"dev,seo,design"}'

# 3. Installer le hook dans le repo git
curl -s https://<host>/install-hook -H "X-API-Key: <token>" | bash
# puis éditer .git/hooks/post-commit → définir PROJECT_SLUG="acme-site"
```

---

## Ce qu'il ne faut pas faire

- Ne pas modifier `SYSTEM_PROMPT` pour le rendre plus "positif" ou "encourageant"
- Ne pas utiliser `datetime.utcnow()` — toujours `datetime.now(timezone.utc)`
- Ne pas utiliser l'ancienne API SQLAlchemy (`Column`, `relationship` sans `Mapped`)
- Ne pas ajouter de logique métier dans `main.py` — elle va dans `reports.py` ou un module dédié
- Ne pas stocker l'`API_KEY` en clair dans les logs ou les réponses API
- Ne pas ajouter de dépendances lourdes (pas de Celery, pas de Redis, pas de React) — la stack légère est un choix délibéré pour rester dans le free tier Railway
- Ne pas créer de fichiers de documentation supplémentaires — `PLAN.md` et ce `CLAUDE.md` suffisent

---

## Extensions futures envisagées (ne pas implémenter sans demande explicite)

- Import depuis Notion / Linear / GitHub Issues
- Notifications email ou Slack lors de la génération d'un rapport
- Interface de configuration des projets depuis le dashboard (actuellement via API)
- Authentification multi-utilisateurs
- Export PDF des rapports
