"""Kommandozeile von kitrader.  python3 -m kitrader --help"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

from . import betrieb as _betrieb
from . import daten as _daten
from . import einstellungen as _einst
from .autopilot import Abbruch, bauen, modus_pruefen
from .backtest import (als_dict, backtest, kerzen_aus_csv, tabelle_einzel, tabelle_walk_forward, walk_forward)
from .broker.basis import Broker, BrokerFehler
from .broker.papier import PapierBroker
from .hilfen import tabelle, takt_text, zahl, zeit_text
from .journal import HORIZONTE, RICHTUNGEN, Journal
from .kursquelle import WiedergabeQuelle, broker_aus_einstellungen, quelle_aus_einstellungen
from .risiko import NOTAUS_DATEI
from . import ki as _ki
from . import labor as _labor
from . import labor_lang as _labor_lang
from . import lage as _lage
from . import nachrichten as _nachrichten
from . import osiris as _osiris
from . import symbole as _symbole
from . import start as _start
from . import telegram as _telegram
from . import welt as _welt
from .strategie import STRATEGIEN, strategie_holen


_OFFEN: list = []        # Datenbanken, die am Ende des Befehls geschlossen werden


def _merke(objekt):
    _OFFEN.append(objekt)
    return objekt


# ---------------------------------------------------------------- Broker für die Anzeigebefehle

def _papier_broker(einst: dict, datenordner: Path) -> PapierBroker:
    """Papier-Broker; ohne erreichbare Kursquelle bleibt er offline (zeigt dann die zuletzt bekannten Kurse)."""
    try:
        quelle = quelle_aus_einstellungen(einst)
        return _merke(PapierBroker(quelle.kurs, einst, datenordner / "papier.db", kerzengeber=quelle.kerzen))
    except Exception:
        def offline(symbol):
            raise BrokerFehler("keine Kursquelle verbunden")
        return _merke(PapierBroker(offline, einst, datenordner / "papier.db"))


def _broker(modus: str, einst: dict, datenordner: Path) -> Broker:
    if modus == "papier":
        return _papier_broker(einst, datenordner)
    return broker_aus_einstellungen(einst, demo=(modus == "demo"))


def _modus(args, einst: dict) -> str:
    modus = getattr(args, "modus", None) or einst["modus"]
    if modus not in _einst.MODI:
        raise Abbruch(f"Unbekannte Betriebsart {modus!r}. Erlaubt: {', '.join(_einst.MODI)}")
    return modus


# ---------------------------------------------------------------- Befehle

def cmd_konto(args, einst, ordner) -> int:
    modus = _modus(args, einst)
    k = _broker(modus, einst, ordner).konto()
    print(tabelle(["Konto", "Wert"], [
        ["Betriebsart", modus + (" (Simulation, kein echtes Geld)" if k.papier else " (ECHTES GELD)")],
        ["Währung", k.waehrung], ["Guthaben (bar)", zahl(k.guthaben)], ["Gesamtwert", zahl(k.gesamtwert)],
        ["Kaufkraft", zahl(k.kaufkraft)], ["Ergebnis heute", zahl(k.tagesergebnis)]], links=(0, 1)))
    return 0


def _positionen_tabelle(positionen) -> str:
    if not positionen:
        return "Keine offenen Positionen."
    zeilen = [[p.symbol, "long" if p.menge > 0 else "short", f"{p.menge:+g}", zahl(p.einstand, 4), zahl(p.kurs, 4),
               zahl(p.ergebnis), p.waehrung] for p in positionen]
    return tabelle(["Symbol", "Richtung", "Menge", "Einstand", "Kurs", "Ergebnis (offen)", "Währung"], zeilen, links=(0, 1, 6))


def cmd_positionen(args, einst, ordner) -> int:
    print(_positionen_tabelle(_broker(_modus(args, einst), einst, ordner).positionen()))
    return 0


def _orders_tabelle(stande) -> str:
    if not stande:
        return "Keine Orders."
    zeilen = [[s.id, s.order.symbol, s.order.seite, f"{s.order.menge:g}", s.order.art, zahl(s.order.limit, 4),
               zahl(s.order.stop, 4), s.status, zahl(s.preis, 4), zeit_text(s.zeit), s.meldung or s.order.notiz]
              for s in stande]
    return tabelle(["ID", "Symbol", "Seite", "Menge", "Art", "Limit", "Stop", "Status", "Preis", "Zeit", "Hinweis"],
                   zeilen, links=(0, 1, 2, 4, 7, 10))


def cmd_orders(args, einst, ordner) -> int:
    print(_orders_tabelle(_broker(_modus(args, einst), einst, ordner).orders(nur_offene=not args.alle)))
    return 0


def _trades_tabelle(trades) -> str:
    if not trades:
        return "Noch keine abgeschlossenen Trades."
    zeilen = [[t["id"], t["symbol"], t["richtung"], f"{t['menge']:g}", zahl(t["einstieg_preis"], 4),
               zahl(t["ausstieg_preis"], 4), zahl(t["brutto"]), zahl(t["gebuehren"]), zahl(t["netto"])] for t in trades]
    zeilen.append(["", "Summe", "", "", "", "", zahl(sum(t["brutto"] for t in trades)),
                   zahl(sum(t["gebuehren"] for t in trades)), zahl(sum(t["netto"] for t in trades))])
    return tabelle(["Nr", "Symbol", "Richtung", "Menge", "Einstieg", "Ausstieg", "Brutto", "Gebühren", "Netto (FIFO)"],
                   zeilen, links=(1, 2))


def cmd_papier(args, einst, ordner) -> int:
    b = _papier_broker(einst, ordner)
    if args.aktion == "zuruecksetzen":
        b.zuruecksetzen()
        print(f"Papierkonto zurückgesetzt auf {zahl(einst['papier']['startguthaben'])} {einst['waehrung']}.")
        return 0
    k = b.konto()
    print(f"Papierkonto (Simulation): Gesamtwert {zahl(k.gesamtwert)} {k.waehrung}, Guthaben {zahl(k.guthaben)}, "
          f"Start {zahl(einst['papier']['startguthaben'])}, Ergebnis heute {zahl(k.tagesergebnis)}\n")
    print(_positionen_tabelle(b.positionen()))
    print("\nOffene Orders:")
    print(_orders_tabelle(b.orders()))
    trades = b.trades()
    print("\nAbgeschlossene Trades" + (f" (letzte 20 von {len(trades)})" if len(trades) > 20 else "") + ":")
    print(_trades_tabelle(trades[-20:]))
    return 0


def cmd_backtest(args, einst, ordner) -> int:
    if args.strategie not in STRATEGIEN:
        raise Abbruch(f"Unbekannte Strategie {args.strategie!r}. Vorhanden: {', '.join(sorted(STRATEGIEN))}")
    if args.train_anteil is not None:
        einst["backtest"]["train_anteil"] = args.train_anteil
    if args.startkapital is not None:
        einst["backtest"]["startkapital"] = args.startkapital
    if args.faltungen is not None:
        einst["backtest"]["faltungen"] = args.faltungen
    kerzen = kerzen_aus_csv(args.csv)
    print(f"{len(kerzen)} Kerzen von {zeit_text(kerzen[0].t)} bis {zeit_text(kerzen[-1].t)} UTC gelesen.\n")
    if args.ohne_teilung:
        e = backtest(strategie_holen(args.strategie, einst), kerzen, einst, args.symbol, args.markt)
        print(json.dumps(als_dict(e), ensure_ascii=False, indent=1, default=str) if args.json else
              tabelle_einzel(e, "Gesamte Daten")
              + "\n\nACHTUNG: ohne zurückgehaltenen Testteil. Das sagt nichts über die Zukunft.")
    else:
        wf = walk_forward(args.strategie, kerzen, einst, args.symbol, args.markt)
        print(json.dumps(als_dict(wf), ensure_ascii=False, indent=1, default=str) if args.json
              else tabelle_walk_forward(wf))
    return 0


def _symbol(wert: str, einst: dict) -> str:
    """Eingabe (BTC, Bitcoin, XBT/EUR ...) -> Symbol wie BTC/EUR; ein Fehler wird zum verständlichen Abbruch (mit Vorschlägen)."""
    try:
        return _symbole.kanonisch(wert, einst)
    except _symbole.SymbolFehler as e:
        raise Abbruch(str(e)) from None


def cmd_autopilot(args, einst, ordner) -> int:
    modus = args.modus or einst["modus"]
    symbole = args.symbole or None
    if symbole and not args.wiedergabe:
        symbole = list(dict.fromkeys(_symbol(x, einst) for x in symbole))
    wiedergabe = None
    if args.wiedergabe:
        # Testlauf mit eigenem Datenordner, damit das echte Papierkonto unberührt bleibt
        modus = "papier"
        kerzen = kerzen_aus_csv(args.wiedergabe)
        symbol = (symbole or einst["autopilot"]["symbole"] or ["TEST"])[0]
        symbole = [symbol]
        takt = takt_text(statistics.median(b.t - a.t for a, b in zip(kerzen, kerzen[1:])))
        einst["autopilot"]["takt"] = takt
        einst["maerkte"][symbol] = args.markt or einst["maerkte"].get(symbol, "krypto")
        ordner = ordner / "wiedergabe"
        ordner.mkdir(parents=True, exist_ok=True)
        wiedergabe = WiedergabeQuelle({symbol: kerzen}, takt, einst["kosten"]["spread_prozent"])
        print(f"WIEDERGABE: {len(kerzen)} Kerzen ({takt}) von {symbol}; eigener Datenordner {ordner}")
    ap = bauen(modus, einst, ordner, symbole, wiedergabe, strategie=args.strategie)
    _merke(ap.journal)
    if isinstance(ap.broker, PapierBroker):
        _merke(ap.broker)
    if einst["ki"]["filter"]:
        try:
            ap.strategie = _ki.KiFilter(ap.strategie, einst, ap.journal, modus, ordner=ordner)
        except _ki.KiFehler as e:
            raise Abbruch(str(e)) from None
        print("KI-FILTER aktiv: Eröffnungssignale gegen die kurzfristige KI-Richtung (1d) werden verworfen; "
              "die Risikoprüfung gilt weiter. Jede KI-Antwort kann bis zu ki.zeitlimit_s dauern und landet im Journal.")
    beobachter = None
    if not wiedergabe:                       # Testläufe mit Wiedergabe melden nichts
        beobachter = _telegram.beobachten(ap, einst, ordner)
        if beobachter:
            beobachter.starten()
            print("TELEGRAM aktiv: Meldungen gehen an deinen Chat" + (" (Wache hört auf 'stop' und 'status')."
                  if einst["telegram"]["wache"] else "."))
    fehler = None
    try:
        ap.laufen(args.schritte)
    except BaseException as e:
        fehler = e
        raise
    finally:
        if beobachter:
            beobachter.beenden(fehler)
    if wiedergabe and hasattr(ap.broker, "trades"):
        print("\nTrades der Wiedergabe:")
        print(_trades_tabelle(ap.broker.trades()))
    return 0


def cmd_notaus(args, einst, ordner) -> int:
    datei = ordner / NOTAUS_DATEI
    if args.aufheben:
        datei.unlink(missing_ok=True)
        print("Not-Aus aufgehoben (Datei NOTAUS gelöscht). Der Autopilot handelt wieder.")
        return 0
    datei.write_text(f"Not-Aus ausgelöst {datetime.now():%Y-%m-%d %H:%M:%S}\n", encoding="utf-8")
    print("#" * 60 + f"\n#  NOT-AUS gesetzt: {datei}\n" + "#" * 60)
    modus = _modus(args, einst)
    if args.alles:
        print("!" * 70 + "\n!  ACHTUNG --alles: Es werden ALLE Orders und ALLE Positionen des ganzen Kontos\n"
              "!  storniert bzw. zum Markt glattgestellt, auch solche, die nicht von kitrader stammen.\n" + "!" * 70)
        symbole = None
    else:
        symbole = _betrieb.eigene_symbole(einst, ordner, modus)
        print("Nur eigene Symbole (autopilot.symbole und vom Autopiloten eröffnete): "
              + (", ".join(sorted(set(symbole))) or "keine bekannt")
              + "\nFremde Orders und Positionen bleiben unberührt. Das ganze Konto: python3 -m kitrader notaus --alles")
    schritte: list[str] = []
    try:
        for s in _broker(modus, einst, ordner).alles_schliessen(symbole=symbole):
            print("  " + s)
            schritte.append(s)
    except BrokerFehler as e:
        print(f"FEHLER beim Schließen über {modus}: {e}\nDer laufende Autopilot schließt seine Positionen beim nächsten Durchlauf, "
              "sobald er die Datei sieht. Prüfe sonst direkt beim Broker!")
        _telegram.melden(f"NOT-AUS per Kommandozeile gesetzt (Betriebsart {modus}), aber das Schließen schlug fehl: {e}", einst, warten=True)
        return 1
    try:
        _merke(Journal(ordner / "journal.db", einst)).ereignis(
            "notaus", f"Not-Aus per Kommandozeile ({modus}{', ganzes Konto' if args.alles else ''})")
    except Exception:
        pass
    print("Ab jetzt blockiert die Risikoprüfung jede Order. Aufheben mit: python3 -m kitrader notaus --aufheben")
    _telegram.melden(f"NOT-AUS per Kommandozeile ausgelöst (Betriebsart {modus}{', GANZES KONTO' if args.alles else ', nur eigene Symbole'}).\n"
                     + "\n".join("- " + s for s in schritte), einst, warten=True)
    return 0


def cmd_journal(args, einst, ordner) -> int:
    j = _merke(Journal(ordner / "journal.db", einst))
    if args.trefferquote:
        quote = j.trefferquote()
        zeilen = [[h, q["n"], q["treffer"], "-" if q["quote"] is None else zahl(q["quote"], 1) + " %", q["offen"],
                   zahl(einst["journal"]["schwellen"][h], 1) + " %"] for h, q in quote.items()]
        print("Trefferquote der Einschätzungen je Horizont (n = Anzahl ausgewertete Einschätzungen)\n")
        print(tabelle(["Horizont", "n", "Treffer", "Trefferquote", "noch offen", "Schwelle"], zeilen))
        if any(0 < q["n"] < 30 for q in quote.values()):
            print("\nHinweis: Mit weniger als 30 Fällen je Horizont ist die Trefferquote kaum aussagekräftig.")
        return 0
    eintraege = j.entscheidungen(args.anzahl)
    if not eintraege:
        print("Das Journal ist noch leer.")
        return 0
    zeilen = []
    for e in eintraege:
        pruefung = "-" if e["pruefung_ok"] is None else ("ok" if e["pruefung_ok"] else "ABGELEHNT: " +
                                                          "; ".join(json.loads(e["gruende"] or "[]")))
        ausf = json.loads(e["ausfuehrung"]) if e["ausfuehrung"] else {}
        zeilen.append([e["id"], (e["zeit"] or "")[:19].replace("T", " "), e["modus"], e["symbol"], e["strategie"],
                       pruefung, ausf.get("status", "-"), zahl(ausf.get("preis"), 4), zahl(e["ergebnis"]), e["notiz"] or ""])
    print(tabelle(["Nr", "Zeit (UTC)", "Modus", "Symbol", "Strategie", "Risikoprüfung", "Status", "Preis", "Ergebnis", "Notiz"],
                  zeilen, links=(1, 2, 3, 4, 5, 6, 9)))
    return 0


def cmd_einschaetzung_ki(args, einst, ordner) -> int:
    """einschaetzung WERT: die KI schätzt kurz (1d), mittel (3M) und lang (1J) ein (ki.py). WERT darf BTC, Bitcoin, XBT/EUR ... sein."""
    wert = _symbol(args.wert, einst)
    if args.kerzen:
        kerzen = kerzen_aus_csv(args.kerzen)
    else:
        try:
            quelle = quelle_aus_einstellungen(einst)
            kerzen = quelle.kerzen(wert, args.takt, args.anzahl)
        except Exception as e:
            raise Abbruch(f"Keine Kerzen für „{args.wert}“ über die Kursquelle abrufbar ({e}). "
                          "Mit --kerzen DATEI.csv (t,o,h,l,c,v) eigene Kerzen angeben.") from None
    j = _merke(Journal(ordner / "journal.db", einst))
    try:
        e = _ki.einschaetzen(wert, kerzen, treiber=args.treiber, einstellungen=einst, journal=j, ordner=ordner)
    except _ki.KiFehler as fehler:
        raise Abbruch(str(fehler)) from None
    print(_ki.als_text(e, mit_kontext=args.kontext))
    return 0


def cmd_einschaetzung(args, einst, ordner) -> int:
    if args.richtung is None:
        return cmd_einschaetzung_ki(args, einst, ordner)
    if args.horizont is None:
        raise Abbruch("Für eine eigene Einschätzung fehlt der Horizont, z. B.: einschaetzung AAPL steigt 1d 'Grund'")
    if args.treiber or args.kerzen:
        raise Abbruch("--treiber und --kerzen gelten nur für die KI-Einschätzung (ohne Richtung und Horizont).")
    wert = _symbol(args.wert, einst)
    j = _merke(Journal(ordner / "journal.db", einst))
    preis = args.preis
    if preis is None:
        try:
            preis = _broker(_modus(args, einst), einst, ordner).kurs(wert).mitte
        except Exception as e:
            raise Abbruch(f"Kein Kurs für „{args.wert}“ abrufbar ({e}). Mit --preis P den Kurs selbst angeben.") from None
    nr = j.einschaetzung(wert, args.richtung, args.horizont, args.begruendung, preis)
    print(f"Einschätzung #{nr} gespeichert: {wert} {args.richtung} in {args.horizont} (Kurs jetzt {zahl(preis, 4)}). "
          "Die Auswertung läuft automatisch im Autopiloten nach Ablauf des Horizonts.")
    return 0


def cmd_osiris(args, einst, ordner) -> int:
    o = _merke(_osiris.Osiris(einst, ordner / "osiris.db"))
    if args.aktion == "abrufen":
        print(f"Hole OSIRIS-Feeds von {einst['osiris']['adresse']} (mit Pausen, das dauert etwa eine halbe Minute) ...")
        try:
            erg = o.abrufen(erzwingen=args.erzwingen)
        except _osiris.OsirisFehler as e:
            raise Abbruch(str(e)) from None
        if not erg["neu"]:
            print(f"Der letzte Schnappschuss ist jünger als {einst['osiris']['mindestabstand_s']} s und wird weiterverwendet "
                  "(neu holen mit --erzwingen).")
        zeilen = [[n, e["status"] or "-", f"{e['bytes'] / 1000:.0f} kB", e["fehler"] or "ok"] for n, e in erg["feeds"].items()]
        print(tabelle(["Feed", "Status", "Größe", "Ergebnis"], zeilen, links=(0, 3)))
        print(f"Schnappschuss #{erg['id']} vom {zeit_text(erg['zeit'])} UTC in {ordner / 'osiris.db'}")
        return 0
    lage = o.lage()
    print(json.dumps(lage.daten, ensure_ascii=False, indent=1, default=str) if args.json else lage.text)
    return 0


def cmd_oberflaeche(args, einst, ordner) -> int:
    from . import oberflaeche
    return oberflaeche.laufen(einst, ordner, args.port, browser=False if args.nicht_oeffnen else None)


def cmd_start(args, einst, ordner) -> int:
    """start: Dashboard, Autopilot, Telegram-Wache und Zeitplan in einem Prozess (start.py)."""
    return _start.befehl(args, einst, ordner)


def cmd_telegram(args, einst, ordner) -> int:
    try:
        if args.aktion == "einrichten":
            return _telegram.einrichten(einst, neu=args.neu)
        if args.aktion == "test":
            if not _telegram.melden(f"kitrader: Testnachricht {datetime.now():%Y-%m-%d %H:%M:%S}", einst, warten=True):
                raise Abbruch("Telegram ist nicht eingerichtet oder ausgeschaltet. Zuerst: python3 -m kitrader telegram einrichten")
            print("Testnachricht abgeschickt (kommt sie nicht an, steht der Grund oben in den Hinweisen).")
            return 0
        _telegram.wache_laufen(einst, ordner, _modus(args, einst))
        return 0
    except _telegram.TelegramFehler as e:
        raise Abbruch(str(e)) from None


def cmd_daten(args, einst, ordner) -> int:
    """daten kraken PAAR: echte Kerzen aus der öffentlichen Kraken-API holen (daten.py)."""
    try:
        r = _daten.holen_kraken(args.paar, args.tage, args.takt, ordner, pause=args.pause,
                                max_abrufe=args.max_abrufe, neu=args.neu)
    except _daten.DatenFehler as e:
        raise Abbruch(str(e)) from None
    zeilen = [[t, r["kerzen"][t], r["luecken"][t], str(r["dateien"][t])] for t in r["dateien"]]
    print(f"\n{r['paar']} (Kraken): {r['abschluesse']} Abschlüsse in {r['abrufe']} Abrufen, {r['von']} bis {r['bis']} UTC")
    print(tabelle(["Takt", "Kerzen", "Lücken", "Datei"], zeilen, links=(0, 3)))
    if not r["vollstaendig"]:
        print(f"\nACHTUNG: nach {r['abrufe']} Abrufen angehalten (--max-abrufe), der Zeitraum ist NICHT vollständig. "
              "Derselbe Befehl setzt fort.")
    print("\nDie Daten sind nur für den privaten Gebrauch. Nie ins Repo legen.")
    return 0


def cmd_labor(args, einst, ordner) -> int:
    """labor: Strategie-Labor (Walk-forward, Vergleich mit Halten und Zufall) auf den geholten Kerzen (labor.py)."""
    try:
        if getattr(args, "labor_art", None) == "lang":
            return _labor_lang.befehl(args, einst, ordner)
        return _labor.befehl(args, einst, ordner)
    except (_labor.LaborFehler, _labor_lang.LangFehler) as e:
        raise Abbruch(str(e)) from None


# ---------------------------------------------------------------- Aufbau

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m kitrader",
                                description="kitrader: private KI-Trading-Software. Standard ist der Papierhandel "
                                            "(Simulation). Kein Erfolgsversprechen: erst Backtest, dann mehrere "
                                            "Wochen Papierhandel, echtes Geld nur mit kleinen Grenzen.")
    sub = p.add_subparsers(dest="befehl", required=True, metavar="BEFEHL")
    modus_arg = lambda s: s.add_argument("--modus", choices=_einst.MODI, help="Betriebsart (Standard: aus den Einstellungen)")

    s = sub.add_parser("konto", help="Kontostand anzeigen"); modus_arg(s); s.set_defaults(f=cmd_konto)
    s = sub.add_parser("positionen", help="offene Positionen anzeigen"); modus_arg(s); s.set_defaults(f=cmd_positionen)
    s = sub.add_parser("orders", help="Orders anzeigen"); modus_arg(s)
    s.add_argument("--alle", action="store_true", help="auch ausgeführte und stornierte"); s.set_defaults(f=cmd_orders)

    s = sub.add_parser("backtest", help="Strategie auf Kerzen aus einer CSV-Datei (t,o,h,l,c,v) testen")
    s.add_argument("strategie", help="Name: " + ", ".join(sorted(STRATEGIEN)))
    s.add_argument("csv", help="CSV-Datei mit t,o,h,l,c,v (t = ISO-Zeit oder Unix-Sekunden)")
    s.add_argument("--symbol", default="TEST"); s.add_argument("--markt", help="Markt für die Handelszeiten (z. B. us, xetra, krypto)")
    s.add_argument("--ohne-teilung", action="store_true", help="alles in einem Lauf, ohne zurückgehaltenen Testteil")
    s.add_argument("--train-anteil", type=float); s.add_argument("--faltungen", type=int)
    s.add_argument("--startkapital", type=float); s.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    s.set_defaults(f=cmd_backtest)

    s = sub.add_parser("papier", help="Papierkonto: Stand, Trades; 'zuruecksetzen' beginnt neu")
    s.add_argument("aktion", nargs="?", choices=["zuruecksetzen"]); s.set_defaults(f=cmd_papier)

    s = sub.add_parser("autopilot", help="Autopilot starten (Standard: Papierhandel)")
    s.add_argument("--modus", choices=_einst.MODI, help="papier (Standard), demo oder echt (braucht Freigabe)")
    s.add_argument("--symbole", nargs="+"); s.add_argument("--strategie", choices=sorted(STRATEGIEN))
    s.add_argument("--schritte", type=int, help="nach so vielen Durchläufen anhalten")
    s.add_argument("--wiedergabe", metavar="CSV", help="Testlauf: Kerzen aus CSV abspielen (eigener Datenordner)")
    s.add_argument("--markt", help="nur mit --wiedergabe: Markt des Symbols (Standard krypto = rund um die Uhr)")
    s.set_defaults(f=cmd_autopilot)

    s = sub.add_parser("notaus", help="NOT-AUS: alles stornieren, alles glattstellen, Handel sperren"); modus_arg(s)
    s.add_argument("--aufheben", action="store_true", help="Sperre wieder aufheben")
    s.add_argument("--alles", action="store_true",
                   help="ACHTUNG: das GANZE Konto glattstellen, auch fremde Positionen (ohne: nur eigene Symbole)")
    s.set_defaults(f=cmd_notaus)

    s = sub.add_parser("journal", help="Entscheidungen bzw. Trefferquote der Einschätzungen")
    s.add_argument("--trefferquote", action="store_true"); s.add_argument("--anzahl", type=int, default=20)
    s.set_defaults(f=cmd_journal)

    s = sub.add_parser("einschaetzung", help="eigene Einschätzung festhalten (einschaetzung AAPL steigt 1d 'Grund') "
                                             "oder die KI einschätzen lassen (einschaetzung AAPL)")
    s.add_argument("wert"); s.add_argument("richtung", nargs="?", choices=RICHTUNGEN)
    s.add_argument("horizont", nargs="?", choices=list(HORIZONTE))
    s.add_argument("begruendung", nargs="?", default=""); s.add_argument("--preis", type=float)
    s.add_argument("--treiber", choices=_einst.KI_TREIBER, help="KI: claude_cli (Standard laut Einstellung), anthropic oder aus (nur Kontext zeigen)")
    s.add_argument("--kerzen", metavar="CSV", help="KI: Kerzen aus Datei (t,o,h,l,c,v) statt aus der Kursquelle")
    s.add_argument("--takt", default="1d", help="KI: Kerzenlänge bei Kursquelle (Standard 1d)")
    s.add_argument("--anzahl", type=int, default=250, help="KI: Anzahl Kerzen bei Kursquelle (Standard 250)")
    s.add_argument("--kontext", action="store_true", help="KI: den Kontext auch dann zeigen, wenn die KI geantwortet hat")
    modus_arg(s); s.set_defaults(f=cmd_einschaetzung)

    s = sub.add_parser("oberflaeche", help="kleine Bedienoberfläche (optional; die Hauptoberfläche ist das Markt-Dashboard: `start`)")
    s.add_argument("--port", type=int, help="Port (Standard aus den Einstellungen, 8850)")
    s.add_argument("--nicht-oeffnen", action="store_true", help="den Browser nicht selbst öffnen")
    s.set_defaults(f=cmd_oberflaeche)

    s = sub.add_parser("start", help="ALLES in einem Befehl: Markt-Dashboard im Browser (Hauptoberfläche), Autopilot (Standard Papier), "
                                     "Telegram-Wache, Zeitplan (OSIRIS, Nachrichten, Welt, Morgenlage); Strg+C beendet alles")
    _start.argumente(s)
    s.set_defaults(f=cmd_start)

    s = sub.add_parser("telegram", help="Telegram: einrichten, Testnachricht, Wache (Not-Aus per Nachricht 'stop')")
    s.add_argument("aktion", choices=["einrichten", "test", "wache"])
    s.add_argument("--neu", action="store_true", help="einrichten: Chat-ID neu ermitteln")
    modus_arg(s); s.set_defaults(f=cmd_telegram)

    s = sub.add_parser("osiris", help="Weltlage aus OSIRIS (osirisai.live): abrufen oder zusammenfassen")
    s.add_argument("aktion", choices=["abrufen", "lage"])
    s.add_argument("--erzwingen", action="store_true", help="abrufen: auch wenn der letzte Schnappschuss noch frisch ist")
    s.add_argument("--json", action="store_true", help="lage: verdichtete Daten als JSON statt Text")
    s.set_defaults(f=cmd_osiris)
    s = sub.add_parser("daten", help="echte Kerzen holen: daten kraken BTC/EUR --takt 5m 1h --tage 90 (öffentliche Kraken-API)")
    s.add_argument("quelle", choices=["kraken"]); s.add_argument("paar", help="z. B. BTC/EUR oder ETH/EUR")
    s.add_argument("--takt", nargs="+", default=["5m"], metavar="TAKT", help="ein oder mehrere Takte: 1m 5m 15m 30m 1h 4h 1d (Standard 5m)")
    s.add_argument("--tage", type=float, default=30.0, help="so viele Tage zurück (Standard 30; bei BTC/EUR 15 bis 40 Abrufe je Tag)")
    s.add_argument("--pause", type=float, default=1.0, help="Pause zwischen zwei Abrufen in Sekunden (Standard 1, höflich)")
    s.add_argument("--max-abrufe", type=int, help="höchstens so viele Abrufe, danach anhalten (Fortsetzen mit demselben Befehl)")
    s.add_argument("--neu", action="store_true", help="nicht fortsetzen, von vorn beginnen")
    s.set_defaults(f=cmd_daten)

    s = sub.add_parser("labor", help="Strategie-Labor: Kandidaten auf echten Kerzen testen (Walk-forward, Halten, Zufall)")
    _labor.argumente(s)
    s.set_defaults(f=cmd_labor)
    unter = s.add_subparsers(dest="labor_art", metavar="lang")
    lang = unter.add_parser("lang", help="langfristig: Trend, Momentum, Bewertung auf Tages- und Monatsreihen über Jahrzehnte "
                                         "(Daten aus kitrader.historie), Walk-forward, Halten, Zufall, Ereignisse")
    _labor_lang.argumente(lang)

    s = sub.add_parser("nachrichten", help="Nachrichten-Radar: weltweite Feeds und GDELT abrufen, Radar zeigen, Meldungen suchen")
    _nachrichten.argumente(s)
    s.set_defaults(f=_nachrichten.laufen)

    s = sub.add_parser("welt", help="Welt-Rotation: wer gewinnt, wer verliert (Regionen und Anlageklassen, 1 Tag/1 Woche/1 Monat)")
    _welt.argumente(s)
    s.set_defaults(f=_welt.laufen)

    s = sub.add_parser("morgenlage", help="Morgenlage: KI-Einschätzung kurz/mittel/lang je Wert der Watchlist, OSIRIS-Veränderungen, "
                                          "KI-Trefferquote (Text, Telegram, morgenlage.json)")
    _lage.argumente(s)
    s.set_defaults(f=_lage.laufen)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        einst = _einst.laden()
        ordner = _einst.datenordner()
        return args.f(args, einst, ordner)
    except (Abbruch, BrokerFehler, _einst.EinstellungsFehler, ValueError, FileNotFoundError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        return 130
    finally:
        _telegram.leeren(5)                  # ausstehende Telegram-Meldungen noch senden
        while _OFFEN:
            try:
                _OFFEN.pop().beenden()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
