# WorkTracer — Plan du projet

## Contexte

Tu gères 3-4 projets sur 3-4 entreprises différentes, avec des rôles multiples (infra, code, SEO, SEA, design…).
Résultat : perte de visibilité, impression de ne pas avancer, aucune trace exploitable, et aucun moyen de rendre compte à des non-tech.

Ce système répond à 3 besoins concrets :
1. **Capturer automatiquement** ce qui est fait (git hooks + saisie manuelle)
2. **Tracer et structurer** toutes les activités par projet/entreprise
3. **Générer automatiquement des rapports** hebdo/mensuel/trimestriel via Claude API — neutres, rigoureux, doubles audience (tech + non-tech)

---

## Architecture cible

```
[Git hook local] ──────────────────────────────┐
[CLI / formulaire web] ─────────────────────── ▶ [API FastAPI] ──▶ [PostgreSQL]
[Fichiers / notes importées manuellement] ──────┘        │
                                                          │
                                              [Scheduler APScheduler]
                                                    │         │
                                              [Claude API]  [Rapports en DB]
                                                          │
                                                    [Dashboard web minimal]
```

---

## Stack technique

| Composant | Choix | Raison |
|---|---|---|
| Backend | Python FastAPI | Simple, async, idéal pour appels Claude |
| Base de données | PostgreSQL | Fourni gratuitement par Railway |
| Scheduler | APScheduler (intégré) | Cron hebdo/mensuel/trimestriel dans le même process |
| LLM | Claude API (Haiku pour hebdo/mensuel, Sonnet pour trimestriel) | Coût minimal |
| Frontend | HTML/HTMX (no-build) | Dashboard léger, zéro overhead JS |
| Hébergement | Railway | Free tier suffisant, Postgres inclus |
| Auth | API key simple (header `X-API-Key`) | Suffisant pour usage solo |

**Coût estimé : 0-5 €/mois** (Railway free tier + ~0.50-2€ Claude API/mois)

---

## Modèle de données

```sql
Company        (id, name, slug)
Project        (id, company_id, name, slug, description, status, roles[])
WorkEntry      (id, project_id, source[git|manual|note], title, body, metadata_json, tags[], created_at)
Report         (id, type[weekly|monthly|quarterly], period_start, period_end,
                content_technical TEXT, content_executive TEXT, generated_at)
```

---

## Fonctionnalités

### 1. Capture des activités

**Git hook (post-commit)**
- Script shell installable par repo : `curl -s https://<host>/install-hook -H "X-API-Key: <token>" | bash`
- Envoie : repo name, branch, commit message, fichiers modifiés, timestamp
- Non-bloquant, fire-and-forget

**API manuelle**
- `POST /log` — saisie rapide depuis terminal
- Exemple : `curl -X POST https://<host>/log -H "X-API-Key: <token>" -H "Content-Type: application/json" -d '{"project":"slug","title":"Refacto auth","tags":["backend"]}'`

**Import de notes**
- `POST /import/text` — colle un bloc de texte brut, l'IA extrait et structure les entrées

### 2. Endpoints API

```
POST /log                    — ajouter une entrée manuelle
POST /webhook/git            — réception git hook
POST /import/text            — import bloc de texte
GET  /entries                — liste (filtres: project, company, date, tags)
POST /reports/generate       — déclencher un rapport manuellement
GET  /reports                — liste des rapports
GET  /reports/{id}           — rapport complet
GET  /projects               — liste projets/entreprises
POST /projects               — créer projet
POST /companies              — créer entreprise
GET  /health                 — healthcheck
GET  /scheduler/jobs         — état des crons
GET  /install-hook           — script d'installation du git hook
```

### 3. Génération de rapports

**3 types automatiques :**

| Type | Fréquence | Source | Modèle Claude |
|---|---|---|---|
| Hebdomadaire | Lundi 7h | WorkEntries de la semaine | claude-haiku-4-5 |
| Mensuel | 1er du mois 7h | WorkEntries du mois | claude-haiku-4-5 |
| Trimestriel | 1er jan/avr/jul/oct | Rapports mensuels du trimestre | claude-sonnet-4-6 |

**Chaque rapport = 2 sections distinctes :**

**Section A — Technique** (pour toi)
- Résumé factuel par projet : ce qui a été fait, volume, nature
- Analyse avant/après : état du projet, progression réelle vs estimée
- Plus-value mesurable ou impact négatif identifié
- Risques techniques détectés (dette, dépendances, manques)
- Signaux faibles (répétitions, domaines non adressés)

**Section B — Exécutive** (pour tes patrons, non-tech)
- Reformulation en langage business des avancées et risques
- Indicateurs simples : vert/orange/rouge par projet
- Pas de jargon technique
- Formulation neutre : ni enthousiaste ni alarmiste
- Valeur business produite ou perdue

**Prompt IA — principes non négociables :**
```
Tu es un auditeur de projet indépendant. Tu n'es ni l'allié ni l'adversaire
du contributeur. Ton seul objectif est la clarté factuelle.
- Aucune complaisance, aucun biais positif
- Aucun blabla psychologique ni motivationnel
- Les faits, les patterns, les risques, les manques
- Si une semaine est improductive, dis-le
- Si une direction est mauvaise, dis-le
- La plus-value se calcule, elle ne se ressent pas
```

### 4. Dashboard web (minimal)

- Page d'accueil : liste des projets avec statut (dernière activité, dernier rapport)
- Page projet : timeline des entrées + rapports générés
- Page rapport : affichage formaté des 2 sections (onglets tech / exécutif)
- Bouton "Générer rapport maintenant"
- Auth : API key dans cookie de session

---

## Structure du projet

```
worktracer/
├── PLAN.md                  ← ce fichier
├── main.py                  # FastAPI app + routes + auth
├── models.py                # SQLAlchemy models
├── database.py              # DB connection + init
├── scheduler.py             # APScheduler (hebdo/mensuel/trimestriel)
├── reports.py               # Logique génération Claude
├── hooks/
│   └── git-hook.sh          # Script hook à installer localement
├── templates/               # HTML HTMX (Jinja2)
│   ├── base.html
│   ├── index.html
│   ├── project.html
│   └── report.html
├── requirements.txt
├── Dockerfile
├── railway.toml
└── .env.example
```

---

## Déploiement Railway (étapes)

1. Aller sur [railway.app](https://railway.app) → New Project
2. Ajouter un service **PostgreSQL** → noter `DATABASE_URL`
3. Ajouter un service **Web** → connecter ton repo GitHub
4. Variables d'env à configurer :
   ```
   DATABASE_URL=postgresql://...
   ANTHROPIC_API_KEY=sk-ant-...
   API_KEY=<ton_token_secret>
   ```
5. `railway up` ou push sur main → déploiement automatique
6. URL publique : `https://worktracer.up.railway.app`

---

## Installation du git hook (côté local, par repo)

```bash
# Dans chaque repo à tracker :
curl -s https://<ton-host>/install-hook \
  -H "X-API-Key: <ton_token>" | bash
```

---

## Utilisation rapide (CLI)

```bash
# Logger une action manuelle
curl -X POST https://<host>/log \
  -H "X-API-Key: <token>" \
  -H "Content-Type: application/json" \
  -d '{"project": "mon-projet", "title": "Fix bug auth", "body": "Corrigé le token expiry", "tags": ["backend", "fix"]}'

# Importer des notes en vrac
curl -X POST https://<host>/import/text \
  -H "X-API-Key: <token>" \
  -H "Content-Type: application/json" \
  -d '{"project": "mon-projet", "text": "Aujourd'\''hui j'\''ai bossé sur le SEO, ajouté des meta tags, optimisé les images..."}'

# Générer un rapport maintenant
curl -X POST "https://<host>/reports/generate?type=weekly" \
  -H "X-API-Key: <token>"
```

---

## Coût estimé

| Service | Coût |
|---|---|
| Railway (free tier) | 0 €/mois (500h compute) |
| Railway (hobby) | ~5 €/mois si dépassement |
| Claude Haiku (rapports hebdo+mensuel) | ~0.20-0.50 €/mois |
| Claude Sonnet (rapports trimestriels) | ~0.20-0.50 €/an |
| **Total** | **0-6 €/mois** |
