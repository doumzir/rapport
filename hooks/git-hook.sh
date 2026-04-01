#!/bin/bash
# WorkTracer — post-commit hook
#
# INSTALLATION (dans chaque repo à tracker) :
#   curl -s https://<ton-host>/install-hook -H "X-API-Key: <ton_token>" | bash
#
# Puis édite .git/hooks/post-commit et définis PROJECT_SLUG.

# ── CONFIG À MODIFIER ─────────────────────────────────────────────────────────
WORKTRACER_HOST="https://worktracer.up.railway.app"
WORKTRACER_API_KEY="YOUR_API_KEY"
PROJECT_SLUG=""  # ex: "mon-projet-site" — doit correspondre au slug dans WorkTracer
# ─────────────────────────────────────────────────────────────────────────────

if [ -z "$PROJECT_SLUG" ]; then
  echo "[WorkTracer] ⚠ PROJECT_SLUG non défini — hook ignoré. Édite .git/hooks/post-commit" >&2
  exit 0
fi

REPO=$(basename "$(git rev-parse --show-toplevel)" 2>/dev/null || echo "unknown")
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
COMMIT_HASH=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
COMMIT_MSG=$(git log -1 --pretty=%B 2>/dev/null | head -5 | tr '\n' ' ' | sed 's/"/\\"/g')
AUTHOR=$(git log -1 --pretty=format:'%an' 2>/dev/null || echo "unknown")

# Récupère les fichiers modifiés (max 20), formatés en JSON array
FILES_RAW=$(git diff-tree --no-commit-id -r --name-only HEAD 2>/dev/null | head -20)
FILES_JSON="["
while IFS= read -r f; do
  [ -z "$f" ] && continue
  FILES_JSON+="\"$(echo "$f" | sed 's/"/\\"/g')\","
done <<< "$FILES_RAW"
FILES_JSON="${FILES_JSON%,}]"
[ "$FILES_JSON" = "" ] && FILES_JSON="[]"

# Envoi en arrière-plan pour ne pas bloquer le commit
curl -s -X POST "${WORKTRACER_HOST}/webhook/git" \
  -H "X-API-Key: ${WORKTRACER_API_KEY}" \
  -H "Content-Type: application/json" \
  --max-time 5 \
  -d "{
    \"project\": \"${PROJECT_SLUG}\",
    \"repo\": \"${REPO}\",
    \"branch\": \"${BRANCH}\",
    \"commit_hash\": \"${COMMIT_HASH}\",
    \"commit_message\": \"${COMMIT_MSG}\",
    \"files_changed\": ${FILES_JSON},
    \"author\": \"${AUTHOR}\"
  }" > /dev/null 2>&1 &

exit 0
