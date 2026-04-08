"""Logique métier du module de pointage (arrivée/départ/pause)."""
import calendar
from datetime import date, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models import TimeEntry

DAILY_MINUTES = 7 * 60  # 35h/semaine = 7h/jour

BREAK_OPTIONS = {
    "1h":       ("1h de pause", 60),
    "1h30":     ("1h30 de pause", 90),
    "no_break": ("Pas de pause ce midi", 0),
    "other":    ("Autre (préciser)", None),  # minutes = break_note parsed or 0
}


# ── Calculs ───────────────────────────────────────────────────────────────────

def calc_worked_minutes(
    arrival: Optional[time],
    departure: Optional[time],
    break_minutes: int,
    extra_break_minutes: int = 0,
) -> Optional[int]:
    """Retourne les minutes travaillées, ou None si incomplet."""
    if arrival is None or departure is None:
        return None
    arr = arrival.hour * 60 + arrival.minute
    dep = departure.hour * 60 + departure.minute
    if dep <= arr:
        return 0
    return max(0, dep - arr - break_minutes - extra_break_minutes)


def fmt_minutes(total: Optional[int]) -> str:
    """Formate des minutes en 'Xh YY' ou '—'."""
    if total is None:
        return "—"
    sign = "-" if total < 0 else ""
    total = abs(total)
    h = total // 60
    m = total % 60
    return f"{sign}{h}h{m:02d}"


def fmt_minutes_days(total: Optional[int]) -> str:
    """Ex: -13h00 → -1j 6h00."""
    if total is None:
        return "—"
    sign = "-" if total < 0 else "+"
    total_abs = abs(total)
    days = total_abs // DAILY_MINUTES
    remaining = total_abs % DAILY_MINUTES
    h = remaining // 60
    m = remaining % 60
    if days > 0:
        return f"{sign}{days}j {h}h{m:02d}"
    return f"{sign}{h}h{m:02d}"


def get_working_days(year: int, month: int, up_to: Optional[date] = None) -> list[date]:
    """Retourne les jours ouvrés (dim-jeu, weekend algérien = ven+sam) d'un mois, jusqu'à up_to inclus."""
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    if up_to is not None:
        last = min(last, up_to)
    if last < first:
        return []
    days = []
    current = first
    while current <= last:
        if current.weekday() not in (4, 5):  # 4=vendredi, 5=samedi
            days.append(current)
        current += timedelta(1)
    return days


# ── Stats mensuelles ──────────────────────────────────────────────────────────

def get_monthly_stats(db: Session, year: int, month: int) -> dict:
    today = date.today()
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])

    all_working_days = get_working_days(year, month)
    elapsed_working_days = get_working_days(year, month, up_to=today)

    target_minutes = len(elapsed_working_days) * DAILY_MINUTES

    entries = (
        db.query(TimeEntry)
        .filter(TimeEntry.date >= first, TimeEntry.date <= last)
        .order_by(TimeEntry.date)
        .all()
    )
    entries_by_date = {e.date: e for e in entries}

    actual_minutes = 0
    for e in entries:
        wm = calc_worked_minutes(e.arrival_time, e.departure_time, e.break_minutes, e.extra_break_minutes)
        if wm is not None:
            actual_minutes += wm

    balance_minutes = actual_minutes - target_minutes

    # Calcul du solde en jours
    balance_days = balance_minutes / DAILY_MINUTES

    return {
        "total_working_days": len(all_working_days),
        "elapsed_working_days": len(elapsed_working_days),
        "target_minutes": target_minutes,
        "actual_minutes": actual_minutes,
        "balance_minutes": balance_minutes,
        "balance_days": balance_days,
        "entries_by_date": entries_by_date,
        "all_working_days": all_working_days,
        # formatted
        "target_fmt": fmt_minutes(target_minutes),
        "actual_fmt": fmt_minutes(actual_minutes),
        "balance_fmt": fmt_minutes(balance_minutes),
        "balance_days_fmt": fmt_minutes_days(balance_minutes),
    }


# ── Seed des jours passés ─────────────────────────────────────────────────────

def seed_past_days(db: Session, year: int, month: int) -> int:
    """
    Insère les jours ouvrés passés (non encore saisis) :
    - Avant-hier et plus : 5h travaillées (9h00–15h00, 1h pause)
    - Hier : matin uniquement (9h00–13h00, pas de pause)
    """
    today = date.today()
    yesterday = today - timedelta(1)
    working_days = get_working_days(year, month, up_to=yesterday)

    seeded = 0
    for d in working_days:
        if db.query(TimeEntry).filter(TimeEntry.date == d).first():
            continue  # ne pas écraser

        if d == yesterday:
            entry = TimeEntry(
                date=d,
                arrival_time=time(9, 0),
                departure_time=time(13, 0),
                break_type="no_break",
                break_minutes=0,
                break_note="Seed auto — matin uniquement",
            )
        else:
            entry = TimeEntry(
                date=d,
                arrival_time=time(9, 0),
                departure_time=time(15, 0),
                break_type="1h",
                break_minutes=60,
                break_note="Seed auto — 5h/j",
            )
        db.add(entry)
        seeded += 1

    db.commit()
    return seeded
