"""Harte Risikoregeln. Jede Order muss `pruefen()` bestehen, bevor sie an einen Broker geht.

Das gilt unabhängig davon, ob Strategie, KI oder ein Mensch die Order vorschlägt. Die Funktionen sind rein
(kein Netz, keine Datenbank): alles Nötige steckt in `Lage`. Nur die NOTAUS-Datei wird gelesen.

Eröffnung = die Order vergrößert den Betrag einer Position oder dreht sie um. Reine Verkleinerungen
(Gewinn mitnehmen, Verlust begrenzen, glattstellen) sind von den Eröffnungsregeln ausgenommen, damit
eine Sperre nie verhindert, dass ein Risiko abgebaut wird. Nicht ausgenommen sind Not-Aus, veraltete
Kurse und Handelszeiten.

Zusätzlich zählen offene, noch nicht ausgeführte Eröffnungsorders (`Lage.offene_orders`) bei maximaler Positionszahl,
Positionsgröße und Risiko mit, Kurse in fremder Währung werden mit `Lage.wechselkurs` in die Kontowährung
umgerechnet, und ein Kurs ohne echte Kurszeit (`Kurs.zeit_geschaetzt`) erlaubt keine Eröffnung.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .broker.basis import Kurs, Order, OrderStand, symbol_schluessel
from .einstellungen import mit_standard
from .hilfen import zu_utc

NOTAUS_DATEI = "NOTAUS"
_EPS = 1e-9


@dataclass
class Ergebnis:
    ok: bool
    gruende: list[str] = field(default_factory=list)   # bei ok=False: warum abgelehnt (alle Gründe)

    def __str__(self) -> str:
        return "ok" if self.ok else "abgelehnt: " + "; ".join(self.gruende)


@dataclass
class Lage:
    """Alles, was Risikoprüfung und Strategie über die aktuelle Lage wissen müssen."""
    jetzt: datetime                                   # UTC
    gesamtwert: float
    einstellungen: dict
    symbol: str = ""                                  # Symbol, um das es gerade geht (für Strategien)
    guthaben: float = 0.0
    positionen: dict[str, float] = field(default_factory=dict)   # Symbol -> Menge (negativ = short)
    tagesergebnis: float = 0.0                        # seit Tagesbeginn, negativ = Verlust
    orders_heute: int = 0
    kurs: Kurs | None = None                          # Kurs des Symbols der Order
    notaus: bool = False                              # Not-Aus-Flag zusätzlich zur Datei
    tag_gesperrt: bool = False                        # Tagesverlustgrenze war heute schon erreicht
    datenordner: Path | None = None                   # dort liegt die NOTAUS-Datei (None = nicht prüfen)
    offene_orders: list[OrderStand] = field(default_factory=list)   # beim Broker offene, noch nicht ausgeführte Orders
    orders_unbekannt: bool = False                    # True = die offenen Orders waren nicht lesbar: keine Eröffnung
    konto_waehrung: str = ""                          # Währung des Kontos (für Meldungen)
    # Wert einer Einheit der Kurswährung in Kontowährung: 1.0 bei gleicher oder unbekannter Währung, None = die Währung
    # weicht ab und es gibt keinen Wechselkurs (dann keine Eröffnung).
    wechselkurs: float | None = 1.0

    def menge(self, symbol: str | None = None) -> float:
        symbol = symbol or self.symbol
        if symbol in self.positionen:
            return self.positionen[symbol]
        # Der Broker nennt das Symbol evtl. anders als die Einstellungen ("XBTEUR" statt "BTC/EUR", "AAPL" statt "aapl"):
        # sonst bliebe eine offene Position unsichtbar und der Autopilot würde jede Kerze erneut einsteigen.
        schluessel = symbol_schluessel(symbol)
        return sum(m for n, m in self.positionen.items() if symbol_schluessel(n) == schluessel)


# ---------------------------------------------------------------- Märkte und Handelszeiten

@dataclass
class MarktStatus:
    offen: bool
    minuten_bis_schluss: float | None   # None = kein Handelsschluss (rund um die Uhr) oder geschlossen
    tag: date                           # Handelstag in Ortszeit des Marktes


def _nach_symbol(tabelle: dict, symbol: str):
    """Wert zum Symbol aus einer Tabelle der Einstellungen; auch wenn der Broker es anders schreibt (XBTEUR / BTC/EUR)."""
    if symbol in tabelle:
        return tabelle[symbol]
    schluessel = symbol_schluessel(symbol)
    return next((v for n, v in tabelle.items() if symbol_schluessel(n) == schluessel), None)


def markt_von_symbol(symbol: str, einst: dict) -> str:
    return _nach_symbol(einst.get("maerkte", {}), symbol) or einst["risiko"]["standard_markt"]


def _zone(name: str):
    if name.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Zeitzone {name!r} unbekannt (fehlt das Paket tzdata?)") from None


def _uhrzeit(text: str) -> time:
    h, m = text.split(":")[:2]
    return time(int(h), int(m))


def markt_status(markt: str, jetzt: datetime, einst: dict) -> MarktStatus:
    """Ist der Markt jetzt offen? Wirft ValueError bei unbekanntem Markt oder unbekannter Zeitzone."""
    zeiten = einst["handelszeiten"].get(markt)
    if zeiten is None:
        raise ValueError(f"Für den Markt {markt!r} sind keine Handelszeiten eingestellt")
    jetzt = zu_utc(jetzt)
    if zeiten.get("immer"):
        return MarktStatus(True, None, jetzt.date())
    zone = _zone(zeiten["zeitzone"])
    lokal = jetzt.astimezone(zone)
    if lokal.weekday() not in zeiten["tage"]:
        return MarktStatus(False, None, lokal.date())
    von, bis = _uhrzeit(zeiten["von"]), _uhrzeit(zeiten["bis"])
    if not von <= lokal.time().replace(tzinfo=None) < bis:
        return MarktStatus(False, None, lokal.date())
    schluss = lokal.replace(hour=bis.hour, minute=bis.minute, second=0, microsecond=0)
    return MarktStatus(True, (schluss - lokal).total_seconds() / 60, lokal.date())


def handelstag(markt: str, zeit: datetime, einst: dict) -> date:
    """Kalendertag in Ortszeit des Marktes (für Tageswechsel im Backtest und Autopilot)."""
    zeiten = einst["handelszeiten"].get(markt, {})
    if zeiten.get("immer") or "zeitzone" not in zeiten:
        return zu_utc(zeit).date()
    return zu_utc(zeit).astimezone(_zone(zeiten["zeitzone"])).date()


# ---------------------------------------------------------------- Hilfen

def notaus_aktiv(lage: Lage) -> bool:
    if lage.notaus:
        return True
    return lage.datenordner is not None and (Path(lage.datenordner) / NOTAUS_DATEI).exists()


def signierte_menge(order: Order) -> float:
    return order.menge if order.seite == "kauf" else -order.menge


def endposition(order: Order, lage: Lage) -> float:
    return lage.menge(order.symbol) + signierte_menge(order)


def ist_eroeffnung(order: Order, lage: Lage) -> bool:
    """True, wenn die Order den Positionsbetrag vergrößert oder die Position umdreht."""
    alt = lage.menge(order.symbol)
    neu = alt + signierte_menge(order)
    return abs(neu) > abs(alt) + _EPS or alt * neu < -_EPS


def einstiegspreis(order: Order, kurs: Kurs | None) -> float | None:
    """Preis, mit dem die Prüfung rechnet: Limit, Stop oder aktueller Ask/Bid."""
    if order.art in ("limit", "stop_limit") and order.limit is not None:
        return order.limit
    if order.art == "stop" and order.stop is not None:
        return order.stop
    if kurs is None:
        return None
    if order.seite == "kauf":
        return kurs.ask if kurs.ask is not None else kurs.letzter
    return kurs.bid if kurs.bid is not None else kurs.letzter


def tagesverlust_grenze(lage: Lage) -> float:
    """Die strengere der beiden Grenzen (absolut / Prozent), als positive Zahl."""
    r = lage.einstellungen["risiko"]
    return min(r["tagesverlust_max"], r["tagesverlust_max_prozent"] / 100 * max(lage.gesamtwert, 0.0))


def tagesverlust_erreicht(lage: Lage) -> bool:
    return lage.tagesergebnis <= -tagesverlust_grenze(lage) + _EPS


def ausstehende_eroeffnungen(lage: Lage) -> dict[str, dict[str, float]]:
    """Restmengen der offenen, noch nicht ausgeführten Orders, die eine Position eröffnen oder vergrößern würden:
    {Symbolschlüssel: {"kauf": Menge, "verkauf": Menge}}. Orders gegen eine bestehende Position (Schutz-Stop, Ziel,
    Glattstellen) verkleinern sie nur und zählen nicht; ohne Position zählt jede offene Order als Eröffnung."""
    bestand: dict[str, float] = {}
    for name, menge in lage.positionen.items():
        bestand[symbol_schluessel(name)] = bestand.get(symbol_schluessel(name), 0.0) + menge
    erg: dict[str, dict[str, float]] = {}
    for stand in lage.offene_orders:
        rest = stand.order.menge - stand.ausgefuehrt
        if stand.status not in ("offen", "teilweise") or rest <= _EPS:
            continue
        schluessel = symbol_schluessel(stand.order.symbol)
        pos = bestand.get(schluessel, 0.0)
        if (stand.order.seite == "verkauf" and pos > _EPS) or (stand.order.seite == "kauf" and pos < -_EPS):
            continue
        erg.setdefault(schluessel, {"kauf": 0.0, "verkauf": 0.0})[stand.order.seite] += rest
    return erg


def wechselkurs_gueltig(wk: float | None) -> bool:
    return wk is not None and math.isfinite(wk) and wk > 0


def wechselkurs_fehlt_text(lage: Lage, einst: dict | None = None) -> str:
    von = (lage.kurs.waehrung if lage.kurs is not None and lage.kurs.waehrung else "?").upper()
    nach = (lage.konto_waehrung or (einst or lage.einstellungen).get("waehrung") or "?").upper()
    return (f"kein Wechselkurs {von} -> {nach} (Kurswährung weicht von der Kontowährung ab; weder der Broker noch die "
            f"Einstellung wechselkurse liefert einen Kurs): keine Eröffnung")


def positionsgroesse(gesamtwert: float, risiko_prozent: float, einstieg: float, stop: float,
                     position_max_prozent: float | None = None, schritt: float = 1.0,
                     wechselkurs: float = 1.0) -> float:
    """Menge aus dem Risiko je Trade: Risiko (Geld) / Abstand Einstieg-Stop, begrenzt durch die
    maximale Positionsgröße, abgerundet auf den Mengenschritt. 0, wenn nichts Sinnvolles herauskommt.
    `gesamtwert` ist in Kontowährung, Einstieg und Stop in der Kurswährung; `wechselkurs` rechnet sie um
    (Wert einer Einheit Kurswährung in Kontowährung)."""
    if not (isinstance(wechselkurs, (int, float)) and math.isfinite(wechselkurs) and wechselkurs > 0):
        return 0.0                                       # ohne gültigen Wechselkurs nichts handeln
    einstieg, stop = einstieg * wechselkurs, stop * wechselkurs
    abstand = abs(einstieg - stop)
    if not all(math.isfinite(x) for x in (gesamtwert, risiko_prozent, einstieg, stop, abstand, schritt)):
        return 0.0                                       # NaN/inf (kaputte Kursdaten): lieber nichts handeln
    if gesamtwert <= 0 or einstieg <= 0 or abstand <= 0 or schritt <= 0:
        return 0.0
    menge = gesamtwert * risiko_prozent / 100 / abstand
    if position_max_prozent is not None:
        menge = min(menge, gesamtwert * position_max_prozent / 100 / einstieg)
    menge = math.floor(menge / schritt + _EPS) * schritt
    return round(menge, 10) if menge > 0 else 0.0


def mengenschritt(symbol: str, einst: dict) -> float:
    return _nach_symbol(einst.get("mengenschritte", {}), symbol) or einst["risiko"]["mengenschritt"]


# ---------------------------------------------------------------- die Prüfung

def pruefen(order: Order, lage: Lage) -> Ergebnis:
    """Prüft eine Order gegen alle harten Regeln. Sammelt alle Gründe, nicht nur den ersten."""
    einst = mit_standard(lage.einstellungen)
    r = einst["risiko"]
    gruende: list[str] = []

    # 1. Not-Aus blockiert alles
    if notaus_aktiv(lage):
        gruende.append("Not-Aus ist aktiv: kein Handel")

    # 2. Kurs vorhanden und frisch
    kurs = lage.kurs
    if kurs is None:
        gruende.append("kein Kurs vorhanden: kein Handel")
    else:
        alter = (zu_utc(lage.jetzt) - zu_utc(kurs.zeit)).total_seconds()
        if alter > r["kurs_max_alter_s"]:
            gruende.append(f"Kurs veraltet ({alter:.0f} s alt, erlaubt {r['kurs_max_alter_s']:g} s)")

    # 3. Handelszeiten
    markt = markt_von_symbol(order.symbol, einst)
    status = None
    try:
        status = markt_status(markt, lage.jetzt, einst)
        if not status.offen:
            gruende.append(f"Markt {markt} ist geschlossen")
    except ValueError as e:
        gruende.append(f"Handelszeiten nicht prüfbar: {e}")

    eroeffnung = ist_eroeffnung(order, lage)
    if eroeffnung:
        gruende += _eroeffnungsregeln(order, lage, einst, kurs, status)
    return Ergebnis(not gruende, gruende)


def _eroeffnungsregeln(order: Order, lage: Lage, einst: dict, kurs: Kurs | None,
                       status: MarktStatus | None) -> list[str]:
    r = einst["risiko"]
    g: list[str] = []
    alt = lage.menge(order.symbol)
    neu = alt + signierte_menge(order)
    schluessel = symbol_schluessel(order.symbol)
    ausstehend = ausstehende_eroeffnungen(lage)

    # Kurszeit unbekannt: der Kurs könnte beliebig alt sein, also keine Eröffnung (Schließen bleibt erlaubt)
    if kurs is not None and kurs.zeit_geschaetzt:
        g.append("Kurszeit unbekannt (die Quelle nennt keine echte Zeit, der Kurs gilt als veraltet): keine Eröffnung")

    # offene Orders müssen lesbar sein, sonst weiß man nicht, was schon unterwegs ist
    if lage.orders_unbekannt:
        g.append("offene Orders beim Broker nicht lesbar: keine Eröffnung")

    # Tagesverlust
    if lage.tag_gesperrt:
        g.append("Tagesverlustgrenze wurde heute schon erreicht: keine neuen Positionen bis morgen")
    elif tagesverlust_erreicht(lage):
        g.append(f"Tagesverlustgrenze erreicht ({lage.tagesergebnis:.2f} bei Grenze "
                 f"-{tagesverlust_grenze(lage):.2f}): keine neuen Positionen bis morgen")

    # kurz vor Handelsschluss
    if status is not None and status.minuten_bis_schluss is not None \
            and status.minuten_bis_schluss < r["keine_eroeffnung_min_vor_schluss"]:
        g.append(f"nur noch {status.minuten_bis_schluss:.0f} Min. bis Handelsschluss: keine Eröffnung")

    # Anzahl Positionen und Orders: offene, noch nicht ausgeführte Eröffnungsorders zählen wie Positionen
    belegt = {symbol_schluessel(n) for n, m in lage.positionen.items() if abs(m) > _EPS} | set(ausstehend)
    andere = len(belegt - {schluessel})
    if abs(alt) <= _EPS and andere >= r["positionen_max"]:
        g.append(f"schon {andere} offene Positionen bzw. Eröffnungsorders (erlaubt {r['positionen_max']})")
    if lage.orders_heute >= r["orders_max_pro_tag"]:
        g.append(f"schon {lage.orders_heute} Orders heute (erlaubt {r['orders_max_pro_tag']})")

    # Short
    if neu < -_EPS and not r["short_erlaubt"]:
        g.append("Short ist nicht erlaubt")

    # Währung: Preise in Kontowährung umrechnen; ohne Wechselkurs lässt sich Risiko und Größe nicht prüfen
    wk: float | None = lage.wechselkurs
    if not wechselkurs_gueltig(wk):
        g.append(wechselkurs_fehlt_text(lage, einst))
        wk = None

    # schon unterwegs (offene Eröffnungsorders in dieselbe Richtung) zählt zur neuen Order dazu
    unterwegs = ausstehend.get(schluessel, {}).get(order.seite, 0.0)
    gesamt_menge = order.menge + unterwegs
    neu_mit_offenen = neu + (unterwegs if order.seite == "kauf" else -unterwegs)

    # Stop-Loss, Take-Profit und Risiko je Trade
    preis = einstiegspreis(order, kurs)
    if preis is not None and not math.isfinite(preis):
        preis = None                                   # NaN/inf: damit lässt sich nichts prüfen (Vergleiche wären immer falsch)
    if order.stop_loss is None:
        if r["stop_loss_pflicht"]:
            g.append("Stop-Loss fehlt (Pflicht bei jeder Eröffnung)")
    elif not math.isfinite(order.stop_loss):
        g.append(f"Stop-Loss {order.stop_loss:g} ist keine gültige Zahl")
    elif preis is not None:
        long = neu > 0
        if (long and order.stop_loss >= preis) or (not long and order.stop_loss <= preis):
            g.append(f"Stop-Loss {order.stop_loss:g} liegt auf der falschen Seite von {preis:g}")
        elif lage.gesamtwert > 0 and wk is not None:
            risiko = abs(preis - order.stop_loss) * gesamt_menge * wk
            erlaubt = lage.gesamtwert * r["risiko_pro_trade_prozent"] / 100
            if risiko > erlaubt + _EPS:
                zusatz = f" (inkl. {unterwegs:g} schon offen bestellt)" if unterwegs > _EPS else ""
                g.append(f"Risiko je Trade {risiko:.2f} ({risiko / lage.gesamtwert * 100:.2f} %){zusatz} "
                         f"über der Grenze {erlaubt:.2f} ({r['risiko_pro_trade_prozent']:g} %)")

    # Take-Profit muss auf der Gewinnseite liegen, sonst wird er sofort ausgeführt (Trade ohne Sinn, nur Kosten)
    if order.take_profit is not None:
        if not math.isfinite(order.take_profit):
            g.append(f"Take-Profit {order.take_profit:g} ist keine gültige Zahl")
        elif preis is not None:
            long = neu > 0
            if (long and order.take_profit <= preis) or (not long and order.take_profit >= preis):
                g.append(f"Take-Profit {order.take_profit:g} liegt auf der falschen Seite von {preis:g}")

    # Positionsgröße
    if preis is None:
        g.append("kein Preis zum Berechnen von Positionsgröße und Risiko")
    elif lage.gesamtwert <= 0:
        g.append("Gesamtwert ist 0 oder negativ")
    elif wk is not None:
        wert = abs(neu_mit_offenen) * preis * wk
        erlaubt = lage.gesamtwert * r["position_max_prozent"] / 100
        if wert > erlaubt + _EPS:
            zusatz = f" (inkl. {unterwegs:g} schon offen bestellt)" if unterwegs > _EPS else ""
            g.append(f"Position {wert:.2f} ({wert / lage.gesamtwert * 100:.1f} % vom Gesamtwert){zusatz} "
                     f"über der Grenze {erlaubt:.2f} ({r['position_max_prozent']:g} %)")
    return g
