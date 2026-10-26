"""Gemeinsame Handgriffe für die Bedienoberfläche und Telegram: Broker öffnen, Übersicht, Not-Aus.

Beides sind nur Fenster auf das, was schon da ist. Hier wird nie eine Order gesendet; die einzige
Ausnahme ist der Not-Aus, der über `broker.alles_schliessen()` alles storniert und glattstellt.
"""
from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

from .autopilot import Abbruch, modus_pruefen
from .broker.basis import Broker, BrokerFehler
from .broker.papier import PapierBroker
from .hilfen import jetzt_utc, zahl, zu_utc
from .journal import Journal
from .kursquelle import broker_aus_einstellungen, quelle_aus_einstellungen
from .risiko import NOTAUS_DATEI, Lage, tagesverlust_erreicht, tagesverlust_grenze

log = logging.getLogger("kitrader.betrieb")

MODUS_TEXT = {"papier": "PAPIER", "demo": "DEMO", "echt": "ECHTGELD"}


def papier_broker(einst: dict, ordner: Path) -> PapierBroker:
    """Papier-Broker; ohne erreichbare Kursquelle bleibt er offline (zeigt dann die zuletzt bekannten Kurse)."""
    try:
        quelle = quelle_aus_einstellungen(einst)
        return PapierBroker(quelle.kurs, einst, Path(ordner) / "papier.db", kerzengeber=quelle.kerzen,
                            wechselkursgeber=getattr(quelle, "wechselkurs", None))
    except Exception:
        def offline(symbol):
            raise BrokerFehler("keine Kursquelle verbunden")
        return PapierBroker(offline, einst, Path(ordner) / "papier.db")


def broker_bauen(modus: str, einst: dict, ordner: Path) -> Broker:
    if modus == "papier":
        return papier_broker(einst, ordner)
    return broker_aus_einstellungen(einst, demo=(modus == "demo"))


@contextlib.contextmanager
def broker_oeffnen(modus: str, einst: dict, ordner: Path, bauer: Callable[[], Broker] | None = None):
    """Broker für kurze Zeit öffnen und danach sauber schließen. `bauer` ersetzt den Standardaufbau (Tests)."""
    broker = bauer() if bauer else broker_bauen(modus, einst, ordner)
    try:
        yield broker
    finally:
        if hasattr(broker, "beenden"):
            with contextlib.suppress(Exception):
                broker.beenden()


def eigene_symbole(einst: dict, ordner: Path, modus: str) -> list[str]:
    """Symbole, die kitrader gehören: `autopilot.symbole` plus alles, was der Autopilot in dieser Betriebsart eröffnet
    hat (Journal, Tabelle `eigene`). Nur diese fasst der Not-Aus ohne `alles` an."""
    symbole = list(einst["autopilot"]["symbole"])
    try:
        j = Journal(Path(ordner) / "journal.db", einst)
        try:
            symbole += [e["symbol"] for e in j.eigene(modus)]
        finally:
            j.beenden()
    except Exception as e:
        log.warning("Journal: eigene Symbole nicht lesbar (%s)", e)
    return symbole


def notaus_stand(ordner: Path) -> dict:
    datei = Path(ordner) / NOTAUS_DATEI
    try:
        seit = datetime.fromtimestamp(datei.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        return {"aktiv": True, "datei": str(datei), "seit": seit}
    except OSError:
        return {"aktiv": False, "datei": str(datei), "seit": None}


# ---------------------------------------------------------------- Übersicht

def _grenze(name: str, aktuell: str, grenze: str, anteil: float | None, status: str, hinweis: str = "") -> dict:
    return {"name": name, "aktuell": aktuell, "grenze": grenze, "anteil": anteil, "status": status, "hinweis": hinweis}


def _status_nach_anteil(anteil: float) -> str:
    return "erreicht" if anteil >= 1 - 1e-9 else ("nah" if anteil >= 0.7 else "ok")


def _grenzen(einst: dict, konto, positionen, tagesergebnis: float, stand: dict, orders_heute: int, jetzt: datetime) -> list[dict]:
    r = einst["risiko"]
    zeilen: list[dict] = []
    if konto is not None:
        try:
            lage = Lage(jetzt=jetzt, gesamtwert=konto.gesamtwert, einstellungen=einst, tagesergebnis=tagesergebnis)
            grenze = tagesverlust_grenze(lage)
            verbraucht = max(0.0, -tagesergebnis)
            anteil = verbraucht / grenze if grenze > 0 else 1.0
            gesperrt = bool(stand.get("gesperrt"))
            status = "erreicht" if gesperrt or tagesverlust_erreicht(lage) else _status_nach_anteil(anteil)
            hinweis = ("Heute gesperrt: keine neuen Positionen. " + (stand.get("grund") or "")).strip() if gesperrt else \
                "Es gilt die strengere der beiden Grenzen (absolut / Prozent vom Gesamtwert)."
            zeilen.append(_grenze("Tagesverlust", f"{zahl(-verbraucht if verbraucht else 0.0)} {konto.waehrung}", f"-{zahl(grenze)} {konto.waehrung}",
                                  min(anteil, 1.0), status, hinweis))
        except Exception as e:
            zeilen.append(_grenze("Tagesverlust", "-", "-", None, "info", f"nicht berechenbar ({e})"))
        offene = [p for p in positionen if p.menge]
        zeilen.append(_grenze("Offene Positionen", str(len(offene)), str(r["positionen_max"]),
                              len(offene) / r["positionen_max"] if r["positionen_max"] else 1.0,
                              _status_nach_anteil(len(offene) / r["positionen_max"]) if r["positionen_max"] else "erreicht"))
        groesste = 0.0
        for p in offene:
            if p.kurs is not None and konto.gesamtwert > 0:
                groesste = max(groesste, abs(p.menge) * p.kurs / konto.gesamtwert * 100)
        zeilen.append(_grenze("Größte Position", f"{zahl(groesste, 1)} %", f"{zahl(r['position_max_prozent'], 1)} %",
                              groesste / r["position_max_prozent"] if r["position_max_prozent"] else None,
                              _status_nach_anteil(groesste / r["position_max_prozent"]) if r["position_max_prozent"] else "info",
                              "Wert der größten Position in Prozent vom Gesamtwert"))
    zeilen.append(_grenze("Orders heute", str(orders_heute), str(r["orders_max_pro_tag"]),
                          orders_heute / r["orders_max_pro_tag"] if r["orders_max_pro_tag"] else None,
                          _status_nach_anteil(orders_heute / r["orders_max_pro_tag"]) if r["orders_max_pro_tag"] else "info",
                          "Gezählt nach UTC-Datum, nur diese Betriebsart"))
    zeilen.append(_grenze("Risiko je Trade", "-", f"{zahl(r['risiko_pro_trade_prozent'], 2)} % vom Gesamtwert", None, "info"))
    zeilen.append(_grenze("Stop-Loss-Pflicht", "-", "an" if r["stop_loss_pflicht"] else "AUS", None,
                          "info" if r["stop_loss_pflicht"] else "nah"))
    zeilen.append(_grenze("Short", "-", "erlaubt" if r["short_erlaubt"] else "verboten", None, "info"))
    zeilen.append(_grenze("Kurs höchstens", "-", f"{zahl(r['kurs_max_alter_s'], 0)} s alt", None, "info"))
    return zeilen


def uebersicht(einst: dict, ordner: Path, modus: str | None = None, bauer: Callable[[], Broker] | None = None,
               jetzt: datetime | None = None) -> dict:
    """Alles für die Übersicht: Betriebsart, Konto, Positionen, Orders, Stand der Risikogrenzen, Not-Aus.
    Jeder Teil fällt für sich aus (mit Hinweis in `fehler`), damit die Betriebsart und der Not-Aus-Stand immer da sind."""
    ordner = Path(ordner)
    modus = modus or einst["modus"]
    jetzt = zu_utc(jetzt or jetzt_utc())
    d: dict = {"modus": modus, "modus_text": MODUS_TEXT.get(modus, modus.upper()), "echtes_geld": modus == "echt",
               "broker": einst["broker"] if modus != "papier" else "papier", "waehrung": einst["waehrung"],
               "stand": jetzt.isoformat(timespec="seconds"), "notaus": notaus_stand(ordner),
               "konto": None, "positionen": [], "orders": [], "grenzen": [], "protokoll": [], "warnungen": [],
               "fehler": [], "freigabe": None}
    konto, positionen = None, []
    try:
        with broker_oeffnen(modus, einst, ordner, bauer) as b:
            konto, positionen, orders = b.konto(), b.positionen(), b.orders(nur_offene=True)
        d["positionen"] = [{"symbol": p.symbol, "richtung": "long" if p.menge > 0 else "short", "menge": p.menge,
                            "einstand": p.einstand, "kurs": p.kurs, "ergebnis": p.ergebnis, "waehrung": p.waehrung}
                           for p in positionen if p.menge]
        d["orders"] = [{"id": s.id, "symbol": s.order.symbol, "seite": s.order.seite, "menge": s.order.menge,
                        "art": s.order.art, "limit": s.order.limit, "stop": s.order.stop, "status": s.status,
                        "zeit": s.zeit.isoformat() if s.zeit else None, "hinweis": s.meldung or s.order.notiz}
                       for s in orders]
        if modus == "demo" and not konto.papier:
            d["warnungen"].append("ACHTUNG: Betriebsart DEMO, aber der Broker meldet ein Echtgeld-Konto.")
        if modus == "echt" and konto.papier:
            d["warnungen"].append("Betriebsart ECHT, aber der Broker meldet ein Demokonto bzw. Simulation.")
    except Exception as e:
        d["fehler"].append(f"Broker ({modus}) nicht lesbar: {e}")
    stand: dict = {}
    orders_heute = 0
    try:
        j = Journal(ordner / "journal.db", einst)
        try:
            datum = jetzt.date().isoformat()
            stand, orders_heute = j.tagesstand(datum), j.orders_heute(datum, modus)
            d["protokoll"] = [{"zeit": e["zeit"], "art": e["art"], "text": e["text"]} for e in j.ereignisse(8)][::-1]
        finally:
            j.beenden()
    except Exception as e:
        d["fehler"].append(f"Journal nicht lesbar: {e}")
    tagesergebnis = 0.0
    if konto is not None:
        if stand.get("startwert") is not None:
            tagesergebnis, quelle = konto.gesamtwert - stand["startwert"], "Autopilot (seit Tagesbeginn, UTC)"
        else:
            tagesergebnis, quelle = konto.tagesergebnis or 0.0, "Broker"
        d["konto"] = {"waehrung": konto.waehrung, "guthaben": konto.guthaben, "gesamtwert": konto.gesamtwert,
                      "kaufkraft": konto.kaufkraft, "tagesergebnis": tagesergebnis, "tagesergebnis_quelle": quelle,
                      "simulation": konto.papier}
    d["tag_gesperrt"] = bool(stand.get("gesperrt"))
    d["grenzen"] = _grenzen(einst, konto, positionen, tagesergebnis, stand, orders_heute, jetzt)
    if modus == "echt":
        try:
            modus_pruefen("echt", einst, ordner)
            d["freigabe"] = {"ok": True, "text": "Echtgeld-Freigabe für heute liegt vor."}
        except Abbruch as e:
            d["freigabe"] = {"ok": False, "text": str(e)}
    return d


def status_text(u: dict) -> str:
    """Kurze Übersicht als Text (Telegram)."""
    z = [f"kitrader - Betriebsart {u['modus_text']}" + (" (ECHTES GELD)" if u["echtes_geld"] else " (kein echtes Geld)")]
    z.append("NOT-AUS AKTIV: der Handel ist gesperrt." if u["notaus"]["aktiv"] else "Not-Aus: nicht aktiv.")
    k = u["konto"]
    if k:
        z.append(f"Gesamtwert {zahl(k['gesamtwert'])} {k['waehrung']}, Guthaben {zahl(k['guthaben'])}, "
                 f"Ergebnis heute {zahl(k['tagesergebnis'])}")
    if u.get("tag_gesperrt"):
        z.append("Tagesverlustgrenze erreicht: heute keine neuen Positionen.")
    pos = u["positionen"]
    z.append(f"Offene Positionen: {len(pos)}" + ("" if not pos else " - " + "; ".join(
        f"{p['symbol']} {p['menge']:+g}" + ("" if p["ergebnis"] is None else f" ({zahl(p['ergebnis'])})") for p in pos[:6])))
    z.append(f"Offene Orders: {len(u['orders'])}")
    z += [f"Warnung: {w}" for w in u["warnungen"]] + [f"Fehler: {f}" for f in u["fehler"]]
    return "\n".join(z)


# ---------------------------------------------------------------- Not-Aus

def notaus_ausloesen(einst: dict, ordner: Path, modus: str | None = None, quelle: str = "Kommandozeile",
                     bauer: Callable[[], Broker] | None = None, melden: bool = True, alles: bool = False) -> dict:
    """Not-Aus: Datei NOTAUS anlegen (sperrt jede Order), dann beim Broker stornieren und glattstellen.
    Ohne `alles` nur die eigenen Symbole (siehe `eigene_symbole`); fremde Orders und Positionen werden nur gemeldet.
    Mit `alles=True` das ganze Konto. Gibt {"ok", "schritte", "modus", "datei", "fehler", "alles"} zurück; wirft nichts.
    Aufheben nur per Kommandozeile (`python3 -m kitrader notaus --aufheben`)."""
    ordner = Path(ordner)
    modus = modus or einst["modus"]
    datei = ordner / NOTAUS_DATEI
    schritte: list[str] = []
    fehler = ""
    try:
        ordner.mkdir(parents=True, exist_ok=True)
        datei.write_text(f"Not-Aus ausgelöst {datetime.now():%Y-%m-%d %H:%M:%S} ({quelle})\n", encoding="utf-8")
        schritte.append(f"Handel gesperrt (Datei {datei} angelegt)")
    except OSError as e:
        fehler = f"Die Datei NOTAUS konnte nicht angelegt werden ({e})."
        schritte.append("FEHLER: " + fehler)
    try:
        symbole = None if alles else eigene_symbole(einst, ordner, modus)
        with broker_oeffnen(modus, einst, ordner, bauer) as b:
            erledigt = list(b.alles_schliessen(symbole=symbole))
            schritte += erledigt or ["Keine offenen Orders und keine offenen Positionen gefunden."]
    except Exception as e:
        schritte.append(f"FEHLER beim Schließen über {modus}: {e}")
        fehler = fehler or f"Schließen über {modus} fehlgeschlagen: {e}"
    if any(s.startswith("FEHLER") for s in schritte):
        fehler = fehler or "Nicht alles konnte geschlossen werden. Beim Broker nachsehen!"
    ergebnis = {"ok": not fehler, "schritte": schritte, "modus": modus, "datei": str(datei), "fehler": fehler,
                "alles": alles}
    try:
        j = Journal(ordner / "journal.db", einst)
        try:
            j.ereignis("notaus", f"Not-Aus per {quelle} ({modus}{', ganzes Konto' if alles else ''})"
                       + (f" - UNVOLLSTÄNDIG: {fehler}" if fehler else ""))
        finally:
            j.beenden()
    except Exception as e:
        log.warning("Journal: Not-Aus nicht vermerkt (%s)", e)
    if melden:
        try:
            from . import telegram
            telegram.melden(notaus_text(ergebnis, quelle), einst)
        except Exception as e:                       # Telegram darf den Not-Aus nie stören
            log.warning("Telegram: Not-Aus nicht gemeldet (%s)", e)
    return ergebnis


def notaus_text(e: dict, quelle: str = "") -> str:
    kopf = f"NOT-AUS ausgelöst" + (f" ({quelle})" if quelle else "") + f", Betriebsart {MODUS_TEXT.get(e['modus'], e['modus'])}."
    z = [kopf] + [f"- {s}" for s in e["schritte"]]
    z.append("ACHTUNG: " + e["fehler"] if e["fehler"] else "Alles erledigt. Der Handel bleibt gesperrt, bis du auf dem Rechner "
             "'python3 -m kitrader notaus --aufheben' ausführst.")
    if any(s.startswith("fremd, nicht angefasst") for s in e["schritte"]):
        z.append("Fremde Orders und Positionen (nicht von kitrader) wurden NICHT angefasst. Für das ganze Konto am Rechner: "
                 "'python3 -m kitrader notaus --alles'.")
    return "\n".join(z)
