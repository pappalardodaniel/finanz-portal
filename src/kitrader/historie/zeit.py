"""Kleine Datumshelfer (ISO-Texte 'JJJJ-MM-TT')."""
from __future__ import annotations

import calendar
from datetime import date, timedelta


def tag(iso: str) -> date:
    return date.fromisoformat(iso[:10])


def tage_zwischen(a: str, b: str) -> int:
    """Tage von a bis b (positiv, wenn b später ist)."""
    return (tag(b) - tag(a)).days


def monate_zwischen(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def monate_addieren(iso: str, n: int) -> str:
    """n Kalendermonate später; fehlt der Tag im Zielmonat, gilt dessen letzter Tag."""
    d = tag(iso)
    m = d.year * 12 + d.month - 1 + n
    j, mo = divmod(m, 12)
    return date(j, mo + 1, min(d.day, calendar.monthrange(j, mo + 1)[1])).isoformat()


def tage_addieren(iso: str, n: int) -> str:
    return (tag(iso) + timedelta(days=n)).isoformat()


def excel_datum(zahl: float) -> str:
    """Excel-Seriennummer (Tage seit 1899-12-30) als ISO-Datum."""
    return (date(1899, 12, 30) + timedelta(days=int(zahl))).isoformat()


def heute() -> date:
    return date.today()
