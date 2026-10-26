"""Kleine Hilfsfunktionen: Zeit, Takte, Tabellen. Nur Standardbibliothek."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

_TAKTE = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}


def jetzt_utc() -> datetime:
    return datetime.now(timezone.utc)


def zu_utc(z: datetime) -> datetime:
    """Zeit nach UTC bringen; Zeiten ohne Zeitzone gelten als UTC."""
    if z.tzinfo is None:
        return z.replace(tzinfo=timezone.utc)
    return z.astimezone(timezone.utc)


def takt_dauer(takt: str) -> timedelta:
    """'1m' -> 1 Minute, '5m', '15m', '1h', '1d'."""
    try:
        return timedelta(seconds=int(takt[:-1]) * _TAKTE[takt[-1]])
    except (ValueError, KeyError, IndexError):
        raise ValueError(f"Unbekannter Takt {takt!r} (erlaubt z. B. 1m, 5m, 15m, 1h, 1d)") from None


def takt_text(dauer: timedelta) -> str:
    """Umkehrung von takt_dauer: 300 s -> '5m'."""
    sek = int(dauer.total_seconds())
    for einheit, faktor in (("d", 86400), ("h", 3600), ("m", 60)):
        if sek % faktor == 0:
            return f"{sek // faktor}{einheit}"
    return f"{sek}s"


def zeit_text(z: datetime | None) -> str:
    return "-" if z is None else zu_utc(z).strftime("%Y-%m-%d %H:%M:%S")


def zahl(x: float | None, stellen: int = 2) -> str:
    """Zahl mit deutschem Dezimalkomma und Tausenderpunkt."""
    if x is None:
        return "-"
    if x == float("inf"):
        return "∞"
    text = f"{x:,.{stellen}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def prozent(x: float | None, stellen: int = 1) -> str:
    return "-" if x is None else zahl(x, stellen) + " %"


def tabelle(kopf: list[str], zeilen: list[list], links: tuple[int, ...] = (0,)) -> str:
    """Einfache Textabelle. Spalten in `links` linksbündig, der Rest rechtsbündig."""
    zeilen = [[("" if z is None else str(z)) for z in zeile] for zeile in zeilen]
    breiten = [len(k) for k in kopf]
    for zeile in zeilen:
        for i, z in enumerate(zeile):
            breiten[i] = max(breiten[i], len(z))

    def fmt(zeile):
        teile = [z.ljust(breiten[i]) if i in links else z.rjust(breiten[i]) for i, z in enumerate(zeile)]
        return "  ".join(teile).rstrip()

    linie = "  ".join("-" * b for b in breiten)
    return "\n".join([fmt(kopf), linie] + [fmt(z) for z in zeilen])
