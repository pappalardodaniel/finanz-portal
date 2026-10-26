"""Echte Kerzen aus der öffentlichen Kraken-API holen (ohne Schlüssel, nur lesen).

`/0/public/OHLC` liefert je Takt nur die letzten 720 Kerzen (bei 5 Minuten gut zweieinhalb Tage). Für längere
Zeiträume holt dieses Modul die einzelnen Handelsabschlüsse (`/0/public/Trades`, höchstens 1000 je Abruf, seitenweise
ab `since`) und verdichtet sie selbst zu Kerzen. Das sind viele Abrufe (BTC/EUR: im Herbst 2026 je nach Handelslage 15.000 bis
40.000 Abschlüsse am Tag, also 15 bis 40 Abrufe je Tag; ETH/EUR etwa die Hälfte), deshalb:

- höchstens ein Abruf je `pause` Sekunden plus Antwortzeit (Standard 1 s), ehrlicher User-Agent;
- bei Ratenbegrenzung oder Netzfehlern längere Pausen und Wiederholung, dann Abbruch mit klarer Meldung;
- `max_abrufe` begrenzt die Zahl der Abrufe;
- Zwischenstand wird regelmäßig gespeichert; ein abgebrochener Lauf setzt mit demselben Befehl fort (`neu=True` beginnt von vorn).

Die Kerzen kommen als CSV (`t,o,h,l,c,v`, t = UTC-Zeit des Kerzenbeginns) nach `<Datenordner>/kerzen/` und sind direkt für
`python3 -m kitrader backtest` und `labor` lesbar. Zeiträume ohne einen einzigen Abschluss ergeben keine Kerze (wie bei
Krakens eigener OHLC-Abfrage); die Zahl solcher Lücken wird gemeldet. Die laufende, noch unfertige Kerze wird verworfen.

Die Daten sind nur für den privaten Gebrauch gedacht (Nutzungsbedingungen von Kraken beachten) und gehören nie ins Repo.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import symbole as _symbole
from .hilfen import takt_dauer

BASIS_URL = "https://api.kraken.com"
USER_AGENT = "kitrader-daten/0.1 (privates Forschungswerkzeug; hoeflich, ca. 1 Abruf/s)"
SEITE = 1000                      # so viele Abschlüsse liefert Kraken höchstens je Abruf
ERLAUBTE_TAKTE = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
KOPFZEILE = "t,o,h,l,c,v"


class DatenFehler(ValueError):
    """Daten ließen sich nicht holen. Der Text ist für Menschen lesbar."""


# ---------------------------------------------------------------- Namen und Dateien

def paar_normal(paar: str) -> str:
    """'BTC/EUR', 'BTC', 'Bitcoin' -> 'XBTEUR' (Kraken nennt Bitcoin XBT; ohne Gegenwährung gilt EUR). Siehe symbole.py."""
    try:
        return _symbole.kraken_name(paar)
    except _symbole.SymbolFehler as e:
        raise DatenFehler(str(e)) from None


def kerzenordner(datenordner: Path | str) -> Path:
    return Path(datenordner) / "kerzen"


def dateiname(altname: str, takt: str) -> str:
    return f"kraken_{altname}_{takt}.csv"


def zeit_iso(sekunden: float) -> str:
    return datetime.fromtimestamp(sekunden, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- Verdichten

class Verdichter:
    """Macht aus zeitlich geordneten Abschlüssen Kerzen eines Takts. Abschluss = [Preis, Menge, Zeit, ...] (Kraken-Format).

    `fertig` sammelt die abgeschlossenen Kerzen (t, o, h, l, c, v); `offen` ist die laufende Kerze.
    Ein Abschluss, der zeitlich vor die laufende Kerze fällt (Kraken liefert streng aufsteigend, das ist nur Vorsicht),
    wird in die laufende Kerze eingerechnet."""

    def __init__(self, takt_s: int):
        if takt_s < 1:
            raise ValueError("takt_s muss mindestens 1 sein")
        self.takt_s = takt_s
        self.fertig: list[tuple] = []
        self.offen: list | None = None          # [t, o, h, l, c, v]

    def zufuegen(self, abschluesse) -> None:
        for a in abschluesse:
            preis, menge, zeit = float(a[0]), float(a[1]), float(a[2])
            if preis <= 0:
                continue
            t = int(zeit // self.takt_s) * self.takt_s
            o = self.offen
            if o is not None and t <= o[0]:                 # gleiche (oder, vorsichtshalber, frühere) Kerze
                o[2], o[3], o[4], o[5] = max(o[2], preis), min(o[3], preis), preis, o[5] + menge
            else:
                if o is not None:
                    self.fertig.append(tuple(o))
                self.offen = [t, preis, preis, preis, preis, menge]

    def luecken(self) -> int:
        """Zahl der fehlenden Kerzen zwischen der ersten und letzten fertigen Kerze."""
        if len(self.fertig) < 2:
            return 0
        return sum(max(0, round((b[0] - a[0]) / self.takt_s) - 1) for a, b in zip(self.fertig, self.fertig[1:]))


def _zeile(k: tuple) -> str:
    return f"{zeit_iso(k[0])},{k[1]!r},{k[2]!r},{k[3]!r},{k[4]!r},{round(k[5], 8)!r}"


def csv_schreiben(pfad: Path, kerzen: list[tuple]) -> None:
    """Schreibt Kerzen als CSV (erst in eine Nebendatei, dann umbenennen: nie eine halbe Datei)."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_name(pfad.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(KOPFZEILE + "\n")
        for k in kerzen:
            f.write(_zeile(k) + "\n")
    os.replace(tmp, pfad)


def csv_lesen(pfad: Path) -> list[tuple]:
    kerzen = []
    with open(pfad, encoding="utf-8") as f:
        next(f, None)
        for zeile in f:
            t, o, h, l, c, v = zeile.strip().split(",")
            sek = datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
            kerzen.append((int(sek), float(o), float(h), float(l), float(c), float(v)))
    return kerzen


# ---------------------------------------------------------------- Abruf

def http_holen(methode: str, parameter: dict, basis_url: str = BASIS_URL, timeout: float = 30.0):
    """Ein öffentlicher Kraken-Abruf. Gibt `result` zurück; wirft DatenFehler (Netz, HTTP, Krakens Fehlerliste)."""
    url = f"{basis_url}/0/public/{methode}?{urllib.parse.urlencode(parameter)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as a:
            text = a.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise _Wiederholbar(f"Kraken antwortet mit HTTP {e.code}", streng=e.code in (429, 403)) from None
    except (urllib.error.URLError, OSError, socket.timeout) as e:
        raise _Wiederholbar(f"Kraken nicht erreichbar: {getattr(e, 'reason', e)}") from None
    try:
        j = json.loads(text)
    except ValueError:
        raise _Wiederholbar(f"Unerwartete Antwort von Kraken: {text[:80]!r}") from None
    fehler = [str(x) for x in (j.get("error") or []) if str(x).startswith("E")]
    if fehler:
        if any("Rate limit" in x or "Too many" in x or "Temporary lockout" in x for x in fehler):
            raise _Wiederholbar("Kraken meldet Ratenbegrenzung: " + "; ".join(fehler), streng=True)
        if any(x.startswith(("EService", "EGeneral:Internal")) for x in fehler):
            raise _Wiederholbar("Kraken meldet: " + "; ".join(fehler))
        raise DatenFehler("Kraken lehnt die Anfrage ab: " + "; ".join(fehler))
    return j.get("result")


class _Wiederholbar(Exception):
    """Vorübergehender Fehler: später nochmal. `streng` = Ratenbegrenzung, dann deutlich länger warten."""

    def __init__(self, text: str, streng: bool = False):
        super().__init__(text)
        self.streng = streng


def _holen_mit_wiederholung(holen, methode, parameter, schlafen, pause, versuche=6):
    """Wiederholt vorübergehende Fehler mit wachsender Wartezeit. Gibt (Ergebnis, neue_pause) zurück."""
    for versuch in range(1, versuche + 1):
        try:
            return holen(methode, parameter), pause
        except _Wiederholbar as e:
            if versuch == versuche:
                raise DatenFehler(f"{e} (nach {versuche} Versuchen aufgegeben; der Zwischenstand ist gespeichert, "
                                  "derselbe Befehl setzt später fort)") from None
            if e.streng:
                pause = min(pause * 1.5 + 0.5, 10.0)              # künftig langsamer
                warten = 30.0 * versuch
            else:
                warten = 5.0 * versuch
            schlafen(warten)
    raise AssertionError("unerreichbar")


def _paar_aufloesen(holen, paar: str, schlafen, pause) -> tuple[str, str]:
    """Altname und Anzeigename des Paars bei Kraken ('BTC/EUR', 'BTC' oder 'Bitcoin' -> ('XBTEUR', 'XBT/EUR')).
    Kennt Kraken den vermuteten Namen nicht, wird die Paarliste befragt; der Fehler nennt dann Vorschläge."""
    ergebnis = None
    try:
        ergebnis, _ = _holen_mit_wiederholung(holen, "AssetPairs", {"pair": paar_normal(paar)}, schlafen, pause)
    except DatenFehler as e:
        if "asset pair" not in str(e).lower():
            raise
    if not ergebnis:
        alle, _ = _holen_mit_wiederholung(holen, "AssetPairs", {}, schlafen, pause)
        try:
            treffer = _symbole.PaarVerzeichnis(holen=lambda: alle or {}).finden(paar)
        except _symbole.SymbolFehler as e:
            raise DatenFehler(str(e)) from None
        ergebnis, _ = _holen_mit_wiederholung(holen, "AssetPairs", {"pair": treffer.altname}, schlafen, pause)
    if not ergebnis:
        raise DatenFehler(f"Kraken kennt das Paar {paar!r} nicht.")
    info = next(iter(ergebnis.values()))
    return info.get("altname") or paar_normal(paar), info.get("wsname") or paar


def holen_kraken(paar: str, tage: float, takte: list[str], datenordner: Path | str, *, holen=None,
                 schlafen=time.sleep, jetzt=time.time, pause: float = 1.0, max_abrufe: int | None = None,
                 neu: bool = False, speichern_alle: int = 50, ausgabe=print) -> dict:
    """Holt die Abschlüsse der letzten `tage` Tage und verdichtet sie zu Kerzen der angegebenen Takte.

    holen(methode, parameter) -> result  (Standard: echter Abruf; für Tests austauschbar)
    Rückgabe: {"paar", "altname", "dateien": {takt: Pfad}, "kerzen": {takt: n}, "luecken": {takt: n}, "abrufe", "abschluesse",
               "von", "bis", "vollstaendig": bool}. `vollstaendig` ist False, wenn max_abrufe vor dem Ende erreicht war."""
    if tage <= 0:
        raise DatenFehler("tage muss größer als 0 sein")
    if not takte:
        raise DatenFehler("Mindestens ein Takt nötig (z. B. 5m)")
    for t in takte:
        if t not in ERLAUBTE_TAKTE:
            raise DatenFehler(f"Takt {t!r} nicht erlaubt (erlaubt: {', '.join(ERLAUBTE_TAKTE)})")
    holen = holen or http_holen
    ordner = kerzenordner(datenordner)
    takte = list(dict.fromkeys(takte))
    sekunden = {t: int(takt_dauer(t).total_seconds()) for t in takte}
    gross = max(sekunden.values())

    altname, anzeige = _paar_aufloesen(holen, paar, schlafen, pause)
    dateien = {t: ordner / dateiname(altname, t) for t in takte}
    zustandsdatei = ordner / f".zustand_kraken_{altname}.json"

    zustand = None
    if not neu and zustandsdatei.is_file():
        try:
            z = json.loads(zustandsdatei.read_text(encoding="utf-8"))
            if z.get("altname") == altname and sorted(z.get("takte", [])) == sorted(takte) \
                    and z.get("tage") == tage and all(d.is_file() for d in dateien.values()):
                zustand = z
            else:
                ausgabe("Ein älterer Zwischenstand passt nicht zu diesem Aufruf (anderes Paar, andere Takte oder andere --tage): "
                        "beginne von vorn.")
        except (ValueError, OSError):
            zustand = None

    verdichter = {t: Verdichter(sekunden[t]) for t in takte}
    abrufe = abschluesse = 0
    if zustand:
        since = int(zustand["since"])
        start_s = int(zustand["start_s"])
        abrufe, abschluesse = int(zustand["abrufe"]), int(zustand["abschluesse"])
        for t in takte:
            v = verdichter[t]
            v.fertig = csv_lesen(dateien[t])
            if zustand["offen"].get(t):
                v.offen = list(zustand["offen"][t])
        ausgabe(f"Setze fort: {abrufe} Abrufe schon gemacht, weiter ab {zeit_iso(since / 1e9)}.")
    else:
        start_s = int((jetzt() - tage * 86400) // gross) * gross          # auf eine volle Kerze des größten Takts gelegt
        since = start_s * 1_000_000_000
    ende_s = jetzt()

    def sichern(abgeschlossen: bool) -> None:
        for t in takte:
            csv_schreiben(dateien[t], verdichter[t].fertig)
        if abgeschlossen:
            zustandsdatei.unlink(missing_ok=True)
            return
        zustandsdatei.write_text(json.dumps({
            "altname": altname, "takte": takte, "tage": tage, "since": since, "start_s": start_s, "abrufe": abrufe,
            "abschluesse": abschluesse, "offen": {t: verdichter[t].offen for t in takte}}), encoding="utf-8")

    vollstaendig = True
    seit_sicherung = 0
    while True:
        if max_abrufe is not None and abrufe >= max_abrufe:
            vollstaendig = False
            break
        ergebnis, pause = _holen_mit_wiederholung(holen, "Trades", {"pair": altname, "since": since, "count": SEITE},
                                                  schlafen, pause)
        abrufe += 1
        seit_sicherung += 1
        schluessel = next((k for k in (ergebnis or {}) if k != "last"), None)
        liste = (ergebnis or {}).get(schluessel) or []
        letzte = int((ergebnis or {}).get("last") or since)
        if liste:
            abschluesse += len(liste)
            for v in verdichter.values():
                v.zufuegen(liste)
        weiter = bool(liste) and letzte != since
        since = letzte
        if abrufe % 50 == 0:
            bis_s = since / 1e9
            fertig_anteil = max(0.0, min(1.0, (bis_s - start_s) / max(1.0, ende_s - start_s)))
            ausgabe(f"  {abrufe} Abrufe, {abschluesse} Abschlüsse, Stand {zeit_iso(bis_s)} ({fertig_anteil * 100:.0f} %)")
        if seit_sicherung >= speichern_alle:
            sichern(False)
            seit_sicherung = 0
        if not weiter or len(liste) < SEITE:          # weniger als eine volle Seite: wir sind beim aktuellen Stand
            break
        schlafen(pause)

    # laufende (unfertige) Kerze verwerfen: sie wird nie in `fertig` übernommen
    sichern(vollstaendig)
    if not vollstaendig:
        sichern(False)
    erste = verdichter[takte[0]].fertig
    return {
        "paar": anzeige, "altname": altname, "dateien": dateien, "abrufe": abrufe, "abschluesse": abschluesse,
        "kerzen": {t: len(verdichter[t].fertig) for t in takte},
        "luecken": {t: verdichter[t].luecken() for t in takte},
        "von": zeit_iso(erste[0][0]) if erste else None, "bis": zeit_iso(erste[-1][0] + sekunden[takte[0]]) if erste else None,
        "vollstaendig": vollstaendig,
    }
