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

| Type rapport | Modèle                      | Raison                         |
| ------------ | --------------------------- | ------------------------------ |
| Hebdomadaire | `claude-haiku-4-5-20251001` | Volume élevé, coût minimal     |
| Mensuel      | `claude-haiku-4-5-20251001` | Idem                           |
| Trimestriel  | `claude-sonnet-4-6`         | Analyse profonde, 1x/trimestre |

### Prompt system — principe fondateur

Le `SYSTEM_PROMPT` s'applique **uniquement à la section technique**.
La section exécutive a ses propres règles (voir ci-dessous) qui priment sur le system prompt.

**Section technique** : neutralité absolue, rigueur factuelle, zéro complaisance, identification des risques.
**Section exécutive** : purement descriptive — ce qui a été produit, rien d'autre. Pas d'opinions, pas de recommandations, pas de jargon technique (commits, branches, etc.).

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

| Variable            | Obligatoire | Description                                                             |
| ------------------- | ----------- | ----------------------------------------------------------------------- |
| `DATABASE_URL`      | oui         | `postgresql://...` (Railway) ou `sqlite:///./worktracer.db` (dev local) |
| `ANTHROPIC_API_KEY` | oui         | Clé API Anthropic                                                       |
| `API_KEY`           | oui         | Secret auth (générer avec `openssl rand -hex 32`)                       |
| `TZ`                | non         | Timezone scheduler (défaut : `Europe/Paris`)                            |

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
curl -s -X POST "https://rapport-production-7ea0.up.railway.app/companies" \
  -H "X-API-Key: e7840acc004da55b637716ea6ba38e58524cbea0d5005809af990e39713291bc" \
  -H "Content-Type: application/json" \
  -d '{"name":"NomEntreprise","slug":"slug-entreprise"}'

# 2. Créer le projet
curl -s -X POST "https://rapport-production-7ea0.up.railway.app/projects" \
  -H "X-API-Key: e7840acc004da55b637716ea6ba38e58524cbea0d5005809af990e39713291bc" \
  -H "Content-Type: application/json" \
  -d '{"company_slug":"slug-entreprise","name":"NomProjet","slug":"slug-projet"}'

# 3. Installer le hook dans le repo (depuis le dossier du repo)
curl -s "https://rapport-production-7ea0.up.railway.app/install-hook?project=slug-projet" \
  -H "X-API-Key: e7840acc004da55b637716ea6ba38e58524cbea0d5005809af990e39713291bc" | bash

# 4. Backfill historique (depuis le dossier du repo)
bash /Users/demdoum/rapportDeTravail/worktracer/hooks/backfill.sh --since "2026-04-01"
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

## Règles de gestion du journal de session

**À chaque début de session**, lire la section `## Journal de session` en bas de ce fichier pour reprendre le contexte exact où la dernière session s'est arrêtée.

**À chaque fin de session** (ou quand l'utilisateur le demande), mettre à jour le journal :

- Ajouter une entrée datée avec ce qui a été fait, l'état actuel, et ce qui reste
- Supprimer les entrées jugées obsolètes (tâches terminées depuis longtemps, contexte dépassé) pour ne pas surcharger
- Conserver maximum 5-8 entrées — au-delà, fusionner les plus anciennes en un résumé court
- L'objectif est un journal **vivant et concis**, pas une archive exhaustive

---

## Extensions futures envisagées (ne pas implémenter sans demande explicite)

- Import depuis Notion / Linear / GitHub Issues
- Notifications email ou Slack lors de la génération d'un rapport
- Interface de configuration des projets depuis le dashboard (actuellement via API)
- Authentification multi-utilisateurs
- Export PDF des rapports

---

## Journal de session

### 2026-04-01 / 04-02 — Session initiale

**Réalisé :**

- Projet WorkTracer créé de zéro : FastAPI + PostgreSQL + APScheduler + Claude API + HTMX
- Déployé sur Railway : `https://rapport-production-7ea0.up.railway.app` ✅
- 5 entreprises et projets créés en DB : mepac, upgradeformation, upgradelearning, betclim, tfc (hotlineservice)
- Hook git installé dans le repo `upgradeformation` (PROJECT_SLUG pré-rempli via `?project=`)

**État actuel :**

- Serveur up, DB connectée, scheduler actif (hebdo lundi 7h / mensuel 1er / trimestriel 1er jan-avr-jul-oct)
- Hook installé sur upgradeformation — les autres repos (mepac, upgradelearning, betclim, tfc) restent à faire
- Backfill historique pas encore lancé sur aucun repo

**Reste à faire :**

1. Installer le hook sur les 4 autres repos (mepac, upgradelearning, betclim, tfc) — commande prête dans la section "Ajout d'un nouveau projet"
2. Lancer le backfill sur chaque repo depuis le bon dossier : `bash /Users/demdoum/rapportDeTravail/worktracer/hooks/backfill.sh --since "2026-04-01"` — à adapter dans `backfill.sh` : mettre le bon `PROJECT_SLUG` en haut du fichier avant chaque lancement
3. Générer les premiers rapports une fois le backfill fait : `curl -s -X POST "https://rapport-production-7ea0.up.railway.app/reports/generate?type=monthly" -H "X-API-Key: e7840acc004da55b637716ea6ba38e58524cbea0d5005809af990e39713291bc"`

## SESSION.md — Mémoire de session

Un fichier `SESSION.md` existe à la racine du projet. Il sert de mémoire entre sessions.

**Au début de chaque session :** lire `SESSION.md` pour reprendre le contexte sans que l'utilisateur ait à tout réexpliquer.

**Pendant la session :** mettre à jour `SESSION.md` au fur et à mesure — quand une tâche est terminée, quand une décision est prise, quand un blocage est identifié.

**Règles de mise à jour :**

- Déplacer les tâches terminées de "En cours / À faire" vers "Fait récemment" (garder les 3-5 dernières)
- Nettoyer "Fait récemment" quand la liste devient trop longue (supprimer les anciennes entrées)
- Garder le fichier court — ce n'est pas un journal exhaustif, c'est un résumé utile
- Ne pas dupliquer ce qui est déjà dans le code ou git log

**Ce qui doit toujours y figurer :**

- Ce qui est en cours ou bloqué
- Les prochaines étapes prioritaires
- Les décisions importantes prises récemment (choix technique, contrainte, etc.)

---
