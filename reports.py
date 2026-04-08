"""
Génération de rapports via Claude API.
Principes : neutralité absolue, rigueur factuelle, pas de complaisance.
"""
import os
import json
from datetime import datetime, timezone, timedelta
from typing import Optional
import anthropic
from sqlalchemy.orm import Session
from models import WorkEntry, Report, Project, Company, ReportType


client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """Tu es un auditeur de projet indépendant et rigoureux.

Règles absolues :
- Tu n'es ni l'allié ni l'adversaire du contributeur. Ton seul objectif est la clarté factuelle.
- Aucune complaisance, aucun biais positif, aucune flatterie.
- Aucun blabla psychologique, motivationnel ou moral.
- Si une période est improductive, dis-le clairement avec les chiffres.
- Si une direction est mauvaise ou risquée, identifie-la sans atténuation.
- La plus-value se mesure et se calcule, elle ne se ressent pas.
- Identifie les signaux faibles : répétitions, domaines non adressés, dettes accumulées.
- Distingue ce qui est fait de ce qui est commencé mais non terminé.
- Un rapport neutre n'est pas un rapport vide : c'est un rapport honnête.

Tu produis TOUJOURS deux sections séparées par le marqueur ---EXECUTIVE--- :
1. Section TECHNIQUE (pour le contributeur lui-même, détaillée, factuelle)
2. Section EXÉCUTIVE (pour des dirigeants non-techniques) — RÈGLES STRICTES pour cette section :
   - PAS de mention de commits, branches, git, pull requests, ou tout terme technique
   - PAS d'opinions, jugements, recommandations, ou évaluations ("insuffisant", "bon rythme", etc.)
   - PAS de conseils, ni de "points d'attention", ni de "risques"
   - UNIQUEMENT : ce qui a été produit, livré, ou réalisé — les faits, rien d'autre
   - Ton neutre et purement descriptif. Le lecteur tire ses propres conclusions."""


def _format_entries_for_prompt(entries: list[WorkEntry]) -> str:
    if not entries:
        return "Aucune entrée de travail enregistrée pour cette période."

    lines = []
    for e in entries:
        meta = ""
        if e.metadata_json:
            try:
                m = json.loads(e.metadata_json)
                if "files_changed" in m:
                    meta = f" | Fichiers: {', '.join(m['files_changed'][:5])}"
                if "branch" in m:
                    meta += f" | Branch: {m['branch']}"
            except Exception:
                pass
        tags = f" [{e.tags}]" if e.tags else ""
        date_str = e.created_at.strftime("%Y-%m-%d %H:%M")
        body = f"\n   {e.body}" if e.body else ""
        lines.append(f"- [{date_str}] ({e.source.value}){tags} {e.title}{meta}{body}")

    return "\n".join(lines)


def _format_reports_for_prompt(reports: list[Report]) -> str:
    if not reports:
        return "Aucun rapport précédent disponible."

    lines = []
    for r in reports:
        lines.append(
            f"\n=== Rapport {r.type.value} du {r.period_start.strftime('%Y-%m-%d')} "
            f"au {r.period_end.strftime('%Y-%m-%d')} ===\n"
            f"{r.content_technical[:2000]}..."
        )
    return "\n".join(lines)


def _build_weekly_prompt(
    entries: list[WorkEntry],
    period_start: datetime,
    period_end: datetime,
    projects_context: str,
) -> str:
    formatted = _format_entries_for_prompt(entries)
    return f"""Génère un rapport hebdomadaire de travail.

Période : du {period_start.strftime('%d/%m/%Y')} au {period_end.strftime('%d/%m/%Y')}
Projets actifs : {projects_context}

Entrées de travail enregistrées cette semaine :
{formatted}

---

SECTION TECHNIQUE — Inclure :
1. Résumé par projet : nombre de commits/tâches, nature du travail (dev, infra, SEO, design…)
2. Progression concrète : qu'est-ce qui a réellement avancé ? Qu'est-ce qui est resté bloqué ?
3. Plus-value produite — ce qui a changé concrètement (fonctionnalités, corrections, infra)
4. Risques ou dettes identifiés dans le code ou les tâches
5. Signaux faibles (tâches répétées, zones non touchées, patterns préoccupants)

Note : les données sont des commits git et des entrées manuelles — NE PAS inventer ni mentionner d'heures travaillées, ce n'est pas tracé.

---EXECUTIVE---

SECTION EXÉCUTIVE — Décrire uniquement ce qui a été produit ou réalisé cette semaine, projet par projet.
Langage accessible, sans jargon technique, sans opinion, sans recommandation.
Liste factuelle uniquement."""


def _build_monthly_prompt(
    entries: list[WorkEntry],
    period_start: datetime,
    period_end: datetime,
    projects_context: str,
) -> str:
    formatted = _format_entries_for_prompt(entries)
    return f"""Génère un rapport mensuel de travail.

Période : {period_start.strftime('%B %Y')}
Projets actifs : {projects_context}

Toutes les entrées de travail du mois :
{formatted}

---

SECTION TECHNIQUE — Inclure :
1. Bilan par projet sur le mois complet : nombre de commits/tâches, nature du travail, répartition
2. Évolution observée : le projet avance-t-il ? Quelles fonctionnalités ou corrections ont abouti ?
3. Plus-value concrète produite : ce qui a changé (fonctionnalités livrées, bugs corrigés, infra améliorée)
4. Impact négatif ou dette technique accumulée visible dans les commits
5. Tendances préoccupantes (répétitions, zones jamais touchées, rythme irrégulier)

Note : les données sont des commits git et des entrées manuelles — NE PAS inventer ni mentionner d'heures travaillées, ce n'est pas tracé.

---EXECUTIVE---

SECTION EXÉCUTIVE — Pour chaque projet, décrire uniquement ce qui a été produit ou réalisé ce mois.
Langage accessible, sans jargon technique, sans opinion, sans recommandation, sans jugement.
Liste factuelle uniquement."""


def _build_quarterly_prompt(
    monthly_reports: list[Report],
    period_start: datetime,
    period_end: datetime,
    projects_context: str,
) -> str:
    formatted = _format_reports_for_prompt(monthly_reports)
    return f"""Génère un rapport trimestriel à partir des rapports mensuels.

Trimestre : {period_start.strftime('%d/%m/%Y')} — {period_end.strftime('%d/%m/%Y')}
Projets concernés : {projects_context}

Rapports mensuels du trimestre :
{formatted}

---

SECTION TECHNIQUE — Inclure :
1. Bilan trimestriel par projet : trajectoire sur 3 mois, cohérence de l'effort
2. Tendances structurelles : quels patterns se répètent ? Lesquels sont préoccupants ?
3. Évaluation de la plus-value produite sur le trimestre (mesurable)
4. Dette technique, risques systémiques, failles identifiées
5. Projection : si la tendance actuelle continue, où en sera chaque projet dans 3 mois ?
6. Ce qui aurait dû être fait et ne l'a pas été

---EXECUTIVE---

SECTION EXÉCUTIVE — Pour chaque projet, décrire uniquement ce qui a été produit ou réalisé ce trimestre.
Langage accessible, sans jargon technique, sans opinion, sans recommandation, sans jugement.
Liste factuelle uniquement."""


def generate_report(
    db: Session,
    report_type: ReportType,
    period_start: datetime,
    period_end: datetime,
    project_id: Optional[int] = None,
) -> Report:
    """Génère un rapport et le persiste en base."""

    # Récupérer le contexte projets
    projects_q = db.query(Project).filter(Project.status == "active")
    if project_id:
        projects_q = projects_q.filter(Project.id == project_id)
    projects = projects_q.all()

    projects_context = ", ".join(
        f"{p.name} ({p.company.name}, rôles: {p.roles or 'non défini'})" for p in projects
    )

    if report_type in (ReportType.weekly, ReportType.monthly):
        entries_q = db.query(WorkEntry).filter(
            WorkEntry.created_at >= period_start,
            WorkEntry.created_at <= period_end,
        )
        if project_id:
            entries_q = entries_q.filter(WorkEntry.project_id == project_id)
        entries = entries_q.order_by(WorkEntry.created_at).all()

        if report_type == ReportType.weekly:
            prompt = _build_weekly_prompt(entries, period_start, period_end, projects_context)
            model = HAIKU_MODEL
        else:
            prompt = _build_monthly_prompt(entries, period_start, period_end, projects_context)
            model = HAIKU_MODEL

    else:  # quarterly
        monthly_reports_q = db.query(Report).filter(
            Report.type == ReportType.monthly,
            Report.period_start >= period_start,
            Report.period_end <= period_end,
        )
        if project_id:
            monthly_reports_q = monthly_reports_q.filter(Report.project_id == project_id)
        monthly_reports = monthly_reports_q.order_by(Report.period_start).all()

        prompt = _build_quarterly_prompt(monthly_reports, period_start, period_end, projects_context)
        model = SONNET_MODEL

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    full_content = response.content[0].text

    # Séparer les deux sections
    if "---EXECUTIVE---" in full_content:
        parts = full_content.split("---EXECUTIVE---", 1)
        content_technical = parts[0].strip()
        content_executive = parts[1].strip()
    else:
        content_technical = full_content
        content_executive = "(Section exécutive non générée — vérifier le prompt)"

    report = Report(
        project_id=project_id,
        type=report_type,
        period_start=period_start,
        period_end=period_end,
        content_technical=content_technical,
        content_executive=content_executive,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def get_weekly_period(ref: Optional[datetime] = None) -> tuple[datetime, datetime]:
    now = ref or datetime.now(timezone.utc)
    start = (now - timedelta(days=now.weekday() + 7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return start, end


def get_monthly_period(ref: Optional[datetime] = None) -> tuple[datetime, datetime]:
    now = ref or datetime.now(timezone.utc)
    # Mois précédent
    first_of_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_of_prev = first_of_this - timedelta(seconds=1)
    first_of_prev = last_of_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first_of_prev, last_of_prev


def get_quarterly_period(ref: Optional[datetime] = None) -> tuple[datetime, datetime]:
    now = ref or datetime.now(timezone.utc)
    quarter = (now.month - 1) // 3
    # Trimestre précédent
    if quarter == 0:
        prev_quarter_start = now.replace(year=now.year - 1, month=10, day=1,
                                          hour=0, minute=0, second=0, microsecond=0)
        prev_quarter_end = now.replace(year=now.year - 1, month=12, day=31,
                                        hour=23, minute=59, second=59, microsecond=0)
    else:
        start_month = (quarter - 1) * 3 + 1
        end_month = start_month + 2
        prev_quarter_start = now.replace(month=start_month, day=1,
                                          hour=0, minute=0, second=0, microsecond=0)
        import calendar
        last_day = calendar.monthrange(now.year, end_month)[1]
        prev_quarter_end = now.replace(month=end_month, day=last_day,
                                        hour=23, minute=59, second=59, microsecond=0)
    return prev_quarter_start, prev_quarter_end
