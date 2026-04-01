import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import anthropic
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, init_db
from models import Company, EntrySource, Project, ProjectStatus, Report, ReportType, WorkEntry
from reports import (
    generate_report,
    get_monthly_period,
    get_quarterly_period,
    get_weekly_period,
)
from scheduler import get_jobs_info, start_scheduler

load_dotenv()

API_KEY = os.getenv("API_KEY", "")


# ── Auth ──────────────────────────────────────────────────────────────────────

def require_api_key(x_api_key: str = Header(...)):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield


app = FastAPI(title="WorkTracer", version="1.0.0", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class CompanyCreate(BaseModel):
    name: str
    slug: str


class ProjectCreate(BaseModel):
    company_slug: str
    name: str
    slug: str
    description: Optional[str] = None
    roles: Optional[str] = None  # ex: "dev,infra,seo"


class LogEntry(BaseModel):
    project: str  # project slug
    title: str
    body: Optional[str] = None
    tags: Optional[str] = None  # comma-separated
    source: Optional[str] = "manual"


class GitWebhookPayload(BaseModel):
    project: str  # project slug
    repo: str
    branch: str
    commit_hash: str
    commit_message: str
    files_changed: list[str] = []
    author: Optional[str] = None
    timestamp: Optional[str] = None


class ImportTextPayload(BaseModel):
    project: str  # project slug
    text: str


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "error"
    return {"status": "ok", "db": db_status}


# ── Companies ─────────────────────────────────────────────────────────────────

@app.post("/companies", dependencies=[Depends(require_api_key)])
def create_company(payload: CompanyCreate, db: Session = Depends(get_db)):
    if db.query(Company).filter(Company.slug == payload.slug).first():
        raise HTTPException(status_code=409, detail="Slug already exists")
    company = Company(name=payload.name, slug=payload.slug)
    db.add(company)
    db.commit()
    db.refresh(company)
    return {"id": company.id, "name": company.name, "slug": company.slug}


@app.get("/companies", dependencies=[Depends(require_api_key)])
def list_companies(db: Session = Depends(get_db)):
    companies = db.query(Company).order_by(Company.name).all()
    return [{"id": c.id, "name": c.name, "slug": c.slug} for c in companies]


# ── Projects ──────────────────────────────────────────────────────────────────

@app.post("/projects", dependencies=[Depends(require_api_key)])
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    company = db.query(Company).filter(Company.slug == payload.company_slug).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if db.query(Project).filter(Project.slug == payload.slug).first():
        raise HTTPException(status_code=409, detail="Slug already exists")
    project = Project(
        company_id=company.id,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        roles=payload.roles,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"id": project.id, "name": project.name, "slug": project.slug}


@app.get("/projects", dependencies=[Depends(require_api_key)])
def list_projects(
    company: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(Project)
    if company:
        c = db.query(Company).filter(Company.slug == company).first()
        if c:
            q = q.filter(Project.company_id == c.id)
    projects = q.order_by(Project.name).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "slug": p.slug,
            "company": p.company.name,
            "status": p.status.value,
            "roles": p.roles,
        }
        for p in projects
    ]


# ── Work entries ──────────────────────────────────────────────────────────────

@app.post("/log", dependencies=[Depends(require_api_key)])
def log_entry(payload: LogEntry, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.slug == payload.project).first()
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{payload.project}' not found")
    source_map = {"manual": EntrySource.manual, "git": EntrySource.git, "note": EntrySource.note}
    entry = WorkEntry(
        project_id=project.id,
        source=source_map.get(payload.source, EntrySource.manual),
        title=payload.title,
        body=payload.body,
        tags=payload.tags,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "created_at": entry.created_at.isoformat()}


@app.post("/webhook/git", dependencies=[Depends(require_api_key)])
def git_webhook(payload: GitWebhookPayload, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.slug == payload.project).first()
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{payload.project}' not found")
    meta = {
        "repo": payload.repo,
        "branch": payload.branch,
        "commit_hash": payload.commit_hash[:8],
        "files_changed": payload.files_changed[:20],
        "author": payload.author,
    }
    entry = WorkEntry(
        project_id=project.id,
        source=EntrySource.git,
        title=payload.commit_message[:500],
        body=f"Commit {payload.commit_hash[:8]} sur {payload.branch} — {len(payload.files_changed)} fichier(s) modifié(s)",
        metadata_json=json.dumps(meta),
        tags="git,commit",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "created_at": entry.created_at.isoformat()}


@app.post("/import/text", dependencies=[Depends(require_api_key)])
def import_text(payload: ImportTextPayload, db: Session = Depends(get_db)):
    """Importe un bloc de texte libre — Claude en extrait des entrées structurées."""
    project = db.query(Project).filter(Project.slug == payload.project).first()
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{payload.project}' not found")

    ai_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = ai_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=(
            "Tu extrais des entrées de travail d'un texte brut. "
            "Réponds UNIQUEMENT avec un JSON valide : une liste d'objets avec les clés "
            '"title" (string, obligatoire), "body" (string, optionnel), "tags" (string, ex: "seo,dev"). '
            "Maximum 10 entrées. Sois factuel, pas d'interprétation."
        ),
        messages=[{"role": "user", "content": f"Texte à analyser :\n{payload.text}"}],
    )

    raw = response.content[0].text.strip()
    # Extraire le JSON même si entouré de markdown
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        raise HTTPException(status_code=500, detail="L'IA n'a pas retourné de JSON valide")

    entries_data = json.loads(match.group())
    created = []
    for item in entries_data[:10]:
        if not item.get("title"):
            continue
        entry = WorkEntry(
            project_id=project.id,
            source=EntrySource.note,
            title=item["title"][:500],
            body=item.get("body"),
            tags=item.get("tags"),
        )
        db.add(entry)
        created.append(item["title"])

    db.commit()
    return {"created": len(created), "entries": created}


@app.get("/entries", dependencies=[Depends(require_api_key)])
def list_entries(
    project: Optional[str] = Query(None),
    company: Optional[str] = Query(None),
    tags: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(WorkEntry).join(Project)
    if project:
        q = q.filter(Project.slug == project)
    if company:
        q = q.join(Company).filter(Company.slug == company)
    if tags:
        q = q.filter(WorkEntry.tags.contains(tags))
    if since:
        q = q.filter(WorkEntry.created_at >= datetime.fromisoformat(since))
    if until:
        q = q.filter(WorkEntry.created_at <= datetime.fromisoformat(until))
    entries = q.order_by(WorkEntry.created_at.desc()).limit(limit).all()
    return [
        {
            "id": e.id,
            "project": e.project.slug,
            "source": e.source.value,
            "title": e.title,
            "body": e.body,
            "tags": e.tags,
            "created_at": e.created_at.isoformat(),
        }
        for e in entries
    ]


# ── Reports ───────────────────────────────────────────────────────────────────

@app.post("/reports/generate", dependencies=[Depends(require_api_key)])
def trigger_report(
    type: ReportType = Query(...),
    project: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    project_id = None
    if project:
        p = db.query(Project).filter(Project.slug == project).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        project_id = p.id

    now = datetime.now(timezone.utc)
    if type == ReportType.weekly:
        period_start, period_end = get_weekly_period(now)
    elif type == ReportType.monthly:
        period_start, period_end = get_monthly_period(now)
    else:
        period_start, period_end = get_quarterly_period(now)

    report = generate_report(db, type, period_start, period_end, project_id)
    return {
        "id": report.id,
        "type": report.type.value,
        "period_start": report.period_start.isoformat(),
        "period_end": report.period_end.isoformat(),
        "generated_at": report.generated_at.isoformat(),
    }


@app.get("/reports", dependencies=[Depends(require_api_key)])
def list_reports(
    type: Optional[ReportType] = Query(None),
    project: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(Report)
    if type:
        q = q.filter(Report.type == type)
    if project:
        p = db.query(Project).filter(Project.slug == project).first()
        if p:
            q = q.filter(Report.project_id == p.id)
    reports = q.order_by(Report.generated_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "type": r.type.value,
            "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat(),
            "generated_at": r.generated_at.isoformat(),
            "project": r.project.slug if r.project else None,
        }
        for r in reports
    ]


@app.get("/reports/{report_id}", dependencies=[Depends(require_api_key)])
def get_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        "id": report.id,
        "type": report.type.value,
        "period_start": report.period_start.isoformat(),
        "period_end": report.period_end.isoformat(),
        "generated_at": report.generated_at.isoformat(),
        "project": report.project.slug if report.project else None,
        "content_technical": report.content_technical,
        "content_executive": report.content_executive,
    }


# ── Scheduler info ────────────────────────────────────────────────────────────

@app.get("/scheduler/jobs", dependencies=[Depends(require_api_key)])
def scheduler_jobs():
    return get_jobs_info()


# ── Git hook install script ───────────────────────────────────────────────────

@app.get("/install-hook", dependencies=[Depends(require_api_key)])
def install_hook(request: Request):
    host = str(request.base_url).rstrip("/")
    api_key = request.headers.get("x-api-key", "YOUR_API_KEY")
    script = f"""#!/bin/bash
# WorkTracer — post-commit hook
# Installe dans .git/hooks/post-commit

WORKTRACER_HOST="{host}"
WORKTRACER_API_KEY="{api_key}"
PROJECT_SLUG=""  # À DÉFINIR : slug du projet dans WorkTracer

if [ -z "$PROJECT_SLUG" ]; then
  echo "[WorkTracer] PROJECT_SLUG non défini dans .git/hooks/post-commit — hook ignoré" >&2
  exit 0
fi

REPO=$(basename "$(git rev-parse --show-toplevel)")
BRANCH=$(git rev-parse --abbrev-ref HEAD)
COMMIT_HASH=$(git rev-parse HEAD)
COMMIT_MSG=$(git log -1 --pretty=%B | head -5 | tr '\\n' ' ')
FILES=$(git diff-tree --no-commit-id -r --name-only HEAD | head -20 | tr '\\n' ',')
AUTHOR=$(git log -1 --pretty=format:'%an')

curl -s -X POST "$WORKTRACER_HOST/webhook/git" \\
  -H "X-API-Key: $WORKTRACER_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d "{{
    \\"project\\": \\"$PROJECT_SLUG\\",
    \\"repo\\": \\"$REPO\\",
    \\"branch\\": \\"$BRANCH\\",
    \\"commit_hash\\": \\"$COMMIT_HASH\\",
    \\"commit_message\\": \\"$COMMIT_MSG\\",
    \\"files_changed\\": [$(echo $FILES | sed 's/,/","/g' | sed 's/^/\\"/' | sed 's/$/\\"/')],
    \\"author\\": \\"$AUTHOR\\"
  }}" > /dev/null 2>&1 &

exit 0
"""
    # Si appelé depuis curl | bash, on installe directement
    install_script = f"""#!/bin/bash
HOOK_PATH=".git/hooks/post-commit"
cat > "$HOOK_PATH" << 'HOOKEOF'
{script}
HOOKEOF
chmod +x "$HOOK_PATH"
echo "[WorkTracer] Hook installé dans $HOOK_PATH"
echo "[WorkTracer] Édite $HOOK_PATH et définis PROJECT_SLUG avec le slug de ton projet."
"""
    return PlainTextResponse(install_script)


# ── Dashboard HTML ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard_index(request: Request, db: Session = Depends(get_db)):
    # Auth via cookie ou query param pour le dashboard
    api_key_cookie = request.cookies.get("api_key", "")
    api_key_query = request.query_params.get("key", "")
    if api_key_cookie != API_KEY and api_key_query != API_KEY:
        return HTMLResponse(_login_page(), status_code=401)

    companies = db.query(Company).order_by(Company.name).all()
    projects = db.query(Project).order_by(Project.name).all()
    recent_entries = (
        db.query(WorkEntry).order_by(WorkEntry.created_at.desc()).limit(10).all()
    )
    recent_reports = (
        db.query(Report).order_by(Report.generated_at.desc()).limit(5).all()
    )
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "companies": companies,
            "projects": projects,
            "recent_entries": recent_entries,
            "recent_reports": recent_reports,
        },
    )


@app.get("/dashboard/project/{slug}", response_class=HTMLResponse)
def dashboard_project(request: Request, slug: str, db: Session = Depends(get_db)):
    api_key_cookie = request.cookies.get("api_key", "")
    api_key_query = request.query_params.get("key", "")
    if api_key_cookie != API_KEY and api_key_query != API_KEY:
        return HTMLResponse(_login_page(), status_code=401)

    project = db.query(Project).filter(Project.slug == slug).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    entries = (
        db.query(WorkEntry)
        .filter(WorkEntry.project_id == project.id)
        .order_by(WorkEntry.created_at.desc())
        .limit(50)
        .all()
    )
    reports = (
        db.query(Report)
        .filter(Report.project_id == project.id)
        .order_by(Report.generated_at.desc())
        .limit(10)
        .all()
    )
    return templates.TemplateResponse(
        "project.html",
        {"request": request, "project": project, "entries": entries, "reports": reports},
    )


@app.get("/dashboard/report/{report_id}", response_class=HTMLResponse)
def dashboard_report(request: Request, report_id: int, db: Session = Depends(get_db)):
    api_key_cookie = request.cookies.get("api_key", "")
    api_key_query = request.query_params.get("key", "")
    if api_key_cookie != API_KEY and api_key_query != API_KEY:
        return HTMLResponse(_login_page(), status_code=401)

    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return templates.TemplateResponse(
        "report.html", {"request": request, "report": report}
    )


def _login_page() -> str:
    return """<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"><title>WorkTracer — Login</title>
<style>
body{font-family:monospace;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#0f0f0f;color:#e0e0e0}
form{display:flex;flex-direction:column;gap:12px;padding:32px;border:1px solid #333;border-radius:8px;background:#1a1a1a}
h2{margin:0 0 8px;font-size:1.2rem}
input{padding:8px 12px;background:#0f0f0f;border:1px solid #444;border-radius:4px;color:#e0e0e0;font-family:monospace}
button{padding:10px;background:#2563eb;border:none;border-radius:4px;color:white;cursor:pointer;font-family:monospace}
</style></head>
<body>
<form action="/login" method="post">
  <h2>WorkTracer</h2>
  <input type="password" name="key" placeholder="API Key" autofocus>
  <button type="submit">Accéder</button>
</form>
</body></html>"""


@app.post("/login")
async def login(request: Request):
    from fastapi.responses import RedirectResponse
    form = await request.form()
    key = form.get("key", "")
    if key != API_KEY:
        return HTMLResponse(_login_page(), status_code=401)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie("api_key", key, httponly=True, samesite="strict")
    return response
