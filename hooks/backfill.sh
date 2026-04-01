#!/bin/bash
# WorkTracer — Backfill historique git
#
# Envoie les commits passés d'un repo vers WorkTracer.
# Usage :
#   cd /chemin/vers/ton/repo
#   bash /chemin/vers/backfill.sh
#
# Options :
#   --since "2025-04-01"   Date de début (défaut : 1er du mois en cours)
#   --dry-run              Affiche les commits sans les envoyer

set -euo pipefail

# ── CONFIG À MODIFIER ─────────────────────────────────────────────────────────
WORKTRACER_HOST="https://worktracer.up.railway.app"
WORKTRACER_API_KEY="YOUR_API_KEY"
PROJECT_SLUG=""  # slug du projet dans WorkTracer (obligatoire)
# ─────────────────────────────────────────────────────────────────────────────

DRY_RUN=false
SINCE=$(date +"%Y-%m-01")  # 1er du mois en cours par défaut

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since) SINCE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "Option inconnue: $1"; exit 1 ;;
  esac
done

# Vérifications
if [ -z "$PROJECT_SLUG" ]; then
  echo "❌ PROJECT_SLUG non défini en haut du script."
  exit 1
fi

if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
  echo "❌ Ce script doit être exécuté dans un repo git."
  exit 1
fi

REPO=$(basename "$(git rev-parse --show-toplevel)")
TOTAL=$(git log --oneline --since="$SINCE" 2>/dev/null | wc -l | tr -d ' ')

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  WorkTracer Backfill"
echo "  Repo    : $REPO"
echo "  Projet  : $PROJECT_SLUG"
echo "  Depuis  : $SINCE"
echo "  Commits : $TOTAL"
[ "$DRY_RUN" = true ] && echo "  Mode    : DRY RUN (rien n'est envoyé)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "$TOTAL" -eq 0 ]; then
  echo "Aucun commit depuis $SINCE."
  exit 0
fi

SUCCESS=0
FAIL=0
COUNT=0

# Lit les commits du plus ancien au plus récent
while IFS=$'\t' read -r HASH TIMESTAMP AUTHOR BRANCH_HINT MESSAGE; do
  COUNT=$((COUNT + 1))

  # Récupère les fichiers de ce commit
  FILES_RAW=$(git diff-tree --no-commit-id -r --name-only "$HASH" 2>/dev/null | head -20)
  FILES_JSON="["
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    FILES_JSON+="\"$(echo "$f" | sed 's/\\/\\\\/g' | sed 's/"/\\"/g')\","
  done <<< "$FILES_RAW"
  FILES_JSON="${FILES_JSON%,}]"
  [ "$FILES_JSON" = "[" ] && FILES_JSON="[]"

  # Sanitize message
  CLEAN_MSG=$(echo "$MESSAGE" | head -c 400 | sed 's/"/\\"/g' | tr -d '\n\r')

  # Récupère la branche au moment du commit (approximation via reflog si dispo)
  BRANCH=$(git name-rev --name-only "$HASH" 2>/dev/null | sed 's/~.*//;s/\^.*//' || echo "unknown")

  printf "  [%3d/%d] %s  %s\n" "$COUNT" "$TOTAL" "${HASH:0:7}" "$CLEAN_MSG"

  if [ "$DRY_RUN" = true ]; then
    continue
  fi

  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${WORKTRACER_HOST}/webhook/git" \
    -H "X-API-Key: ${WORKTRACER_API_KEY}" \
    -H "Content-Type: application/json" \
    --max-time 10 \
    -d "{
      \"project\": \"${PROJECT_SLUG}\",
      \"repo\": \"${REPO}\",
      \"branch\": \"${BRANCH}\",
      \"commit_hash\": \"${HASH}\",
      \"commit_message\": \"${CLEAN_MSG}\",
      \"files_changed\": ${FILES_JSON},
      \"author\": \"$(echo "$AUTHOR" | sed 's/"/\\"/g')\",
      \"timestamp\": \"${TIMESTAMP}\"
    }")

  if [ "$HTTP_STATUS" = "200" ]; then
    SUCCESS=$((SUCCESS + 1))
  else
    FAIL=$((FAIL + 1))
    echo "    ⚠ HTTP $HTTP_STATUS"
  fi

  # Petit délai pour ne pas flood l'API
  sleep 0.2

done < <(git log \
  --since="$SINCE" \
  --reverse \
  --format="%H%x09%aI%x09%an%x09%D%x09%s" \
  2>/dev/null)

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$DRY_RUN" = true ]; then
  echo "  Dry run terminé — $TOTAL commits listés, rien envoyé."
else
  echo "  Terminé : $SUCCESS envoyés, $FAIL erreurs"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
