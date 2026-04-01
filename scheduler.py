"""
Scheduler APScheduler pour les rapports automatiques.
- Hebdomadaire : chaque lundi à 7h
- Mensuel : 1er du mois à 7h
- Trimestriel : 1er janvier, avril, juillet, octobre à 7h
"""
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from database import SessionLocal
from models import ReportType
from reports import generate_report, get_weekly_period, get_monthly_period, get_quarterly_period

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Europe/Paris")


def run_weekly_report():
    logger.info("Scheduler: génération rapport hebdomadaire")
    db = SessionLocal()
    try:
        period_start, period_end = get_weekly_period()
        report = generate_report(db, ReportType.weekly, period_start, period_end)
        logger.info(f"Rapport hebdomadaire généré : id={report.id}")
    except Exception as e:
        logger.error(f"Erreur rapport hebdomadaire: {e}")
    finally:
        db.close()


def run_monthly_report():
    logger.info("Scheduler: génération rapport mensuel")
    db = SessionLocal()
    try:
        period_start, period_end = get_monthly_period()
        report = generate_report(db, ReportType.monthly, period_start, period_end)
        logger.info(f"Rapport mensuel généré : id={report.id}")
    except Exception as e:
        logger.error(f"Erreur rapport mensuel: {e}")
    finally:
        db.close()


def run_quarterly_report():
    logger.info("Scheduler: génération rapport trimestriel")
    db = SessionLocal()
    try:
        period_start, period_end = get_quarterly_period()
        report = generate_report(db, ReportType.quarterly, period_start, period_end)
        logger.info(f"Rapport trimestriel généré : id={report.id}")
    except Exception as e:
        logger.error(f"Erreur rapport trimestriel: {e}")
    finally:
        db.close()


def start_scheduler():
    # Lundi à 7h — rapport de la semaine précédente
    scheduler.add_job(
        run_weekly_report,
        CronTrigger(day_of_week="mon", hour=7, minute=0),
        id="weekly_report",
        replace_existing=True,
    )

    # 1er du mois à 7h — rapport du mois précédent
    scheduler.add_job(
        run_monthly_report,
        CronTrigger(day=1, hour=7, minute=0),
        id="monthly_report",
        replace_existing=True,
    )

    # 1er janvier, avril, juillet, octobre à 7h — rapport du trimestre précédent
    scheduler.add_job(
        run_quarterly_report,
        CronTrigger(month="1,4,7,10", day=1, hour=7, minute=0),
        id="quarterly_report",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler démarré : hebdo (lun 7h), mensuel (1er 7h), trimestriel (1er jan/avr/jul/oct 7h)")


def get_jobs_info() -> list[dict]:
    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "next_run": next_run.isoformat() if next_run else None,
        })
    return jobs
