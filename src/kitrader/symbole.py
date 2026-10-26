"""Symbole erkennen: aus dem, was man tippt, wird ein eindeutiges Symbol, und für Kraken das richtige Handelspaar.

Eingaben wie `BTC`, `btc`, `Bitcoin`, `XBT`, `BTCEUR`, `BTC-EUR`, `BTC/EUR`, `XBT/EUR`, `XXBTZEUR`, `ETH`, `Ether`, `Ethereum`, `SOL`,
`Solana` ergeben das Symbol in der Form `BASIS/GEGEN`, zum Beispiel `BTC/EUR`. Fehlt die Gegenwährung, gilt die Standard-Gegenwährung
(Einstellung `[symbole] gegenwaehrung`, Standard EUR). Kraken nennt Bitcoin `XBT` (Paar `XXBTZEUR`, Altname `XBTEUR`, wsname `XBT/EUR`):
`kraken_name` und `PaarVerzeichnis` übersetzen. Das Verzeichnis liest `/0/public/AssetPairs` (alle Paare in einem Abruf) und merkt es sich
im Arbeitsspeicher und, wenn eine Datei angegeben ist, einen Tag lang auf der Platte.

Aktien und andere Werte, die keine Kryptowährung sind (zum Beispiel `AAPL` bei Interactive Brokers), bleiben unverändert (nur Großschreibung):
Die Umschreibung zu `BASIS/GEGEN` gilt, wenn Kraken die Kursquelle bzw. der Broker ist, oder wenn die Eingabe eindeutig eine Kryptowährung nennt
(Name wie „Bitcoin“ oder ein Paar wie `BTC/EUR`). Unbekanntes ergibt einen `SymbolFehler` mit Vorschlägen („Meintest du …?“).
"""
from __future__ import annotations

import difflib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

BASIS_URL = "https://api.kraken.com"
STANDARD_GEGENWAEHRUNG = "EUR"
GUELTIG_S = 24 * 3600                       # so lange gilt die gespeicherte Paarliste
DATEI_NAME = "kraken_paare.json"
MAX_LAENGE = 24
ERLAUBT = re.compile(r"^[A-Za-z0-9._:/\- ]+$")

# Kryptowährung -> (Klartext, weitere Namen); die Namen werden ohne Leerzeichen, Punkte und Bindestriche verglichen.
COINS: dict[str, tuple[str, tuple[str, ...]]] = {
    "BTC": ("Bitcoin", ("bitcoin", "xbt")),
    "ETH": ("Ethereum", ("ethereum", "ether")),
    "SOL": ("Solana", ("solana",)),
    "XRP": ("XRP", ("ripple",)),
    "ADA": ("Cardano", ("cardano",)),
    "DOGE": ("Dogecoin", ("dogecoin", "xdg")),
    "LTC": ("Litecoin", ("litecoin",)),
    "DOT": ("Polkadot", ("polkadot",)),
    "LINK": ("Chainlink", ("chainlink",)),
    "AVAX": ("Avalanche", ("avalanche",)),
    "POL": ("Polygon", ("polygon", "matic")),
    "XLM": ("Stellar", ("stellar",)),
    "BCH": ("Bitcoin Cash", ("bitcoincash",)),
    "XMR": ("Monero", ("monero",)),
    "ATOM": ("Cosmos", ("cosmos",)),
    "UNI": ("Uniswap", ("uniswap",)),
    "TRX": ("Tron", ("tron",)),
    "ALGO": ("Algorand", ("algorand",)),
    "XTZ": ("Tezos", ("tezos",)),
    "ETC": ("Ethereum Classic", ("ethereumclassic",)),
    "SHIB": ("Shiba Inu", ("shiba", "shibainu")),
    "AAVE": ("Aave", ()),
    "NEAR": ("Near", ()),
    "USDT": ("Tether", ("tether",)),
    "USDC": ("USD Coin", ("usdcoin",)),
}
# Gegenwährungen (Reihenfolge egal; beim Zerlegen zählt der längste passende Schluss)
FIAT = {"EUR": "Euro", "USD": "US-Dollar", "GBP": "Pfund", "CHF": "Franken", "CAD": "Kanadischer Dollar", "AUD": "Australischer Dollar",
        "JPY": "Yen"}
GEGENWAEHRUNGEN = tuple(sorted([*FIAT, "USDT", "USDC", "BTC", "ETH"], key=lambda q: (-len(q), q)))
FIAT_NAMEN = {"EURO": "EUR", "DOLLAR": "USD", "USDOLLAR": "USD", "PFUND": "GBP", "FRANKEN": "CHF", "YEN": "JPY"}

# Name (Großbuchstaben, ohne Trennzeichen) -> Ticker. "Eindeutig" sind Namen, die nur eine Kryptowährung meinen können.
_NAMEN: dict[str, str] = {}
_EINDEUTIG: set[str] = set()
for _t, (_klartext, _namen) in COINS.items():
    _NAMEN[_t] = _t
    for _n in (_klartext, *_namen):
        _k = re.sub(r"[\s._\-]", "", _n).upper()
        _NAMEN[_k] = _t
        if _k != _t:
            _EINDEUTIG.add(_k)
_NAMEN.update(FIAT_NAMEN)
_EINDEUTIG.discard("XRP")
KRAKEN_NAMEN = {"BTC": "XBT", "DOGE": "XDG"}            # unser Ticker -> Name bei Kraken
KANON_AUS_KRAKEN = {v: k for k, v in KRAKEN_NAMEN.items()}
_PUR = re.compile(r"^[A-Z0-9._:/\-]{1,24}$")                   # Aktien und andere Werte, die nicht umgeschrieben werden
_KRAKEN_LANG = re.compile(r"^[XZ]([A-Z]{3})[XZ]([A-Z]{3})$")      # XXBTZEUR, XETHZEUR


class SymbolFehler(ValueError):
    """Ein Symbol ist unbekannt oder ungültig. Der Text ist für Menschen lesbar und nennt, wenn möglich, Vorschläge."""


class AbrufFehler(SymbolFehler):
    """Die Paarliste von Kraken ließ sich nicht abrufen (Netz). Das Symbol selbst ist damit nicht beurteilt."""


@dataclass(frozen=True)
class Zerlegt:
    basis: str                  # Ticker, z. B. BTC
    gegen: str                  # Gegenwährung, z. B. EUR
    gegen_genannt: bool         # die Eingabe nannte die Gegenwährung selbst
    eindeutig: bool             # die Eingabe ist sicher eine Kryptowährung (Name oder Paar mit bekannter Basis und Gegenwährung)

    @property
    def symbol(self) -> str:
        return f"{self.basis}/{self.gegen}"


# ---------------------------------------------------------------- Einstellungen

def gegenwaehrung(einst: dict | None) -> str:
    """Standard-Gegenwährung aus den Einstellungen (`[symbole] gegenwaehrung`), sonst EUR."""
    wert = ((einst or {}).get("symbole") or {}).get("gegenwaehrung")
    return str(wert).strip().upper() if wert else STANDARD_GEGENWAEHRUNG


def nutzt_kraken(einst: dict | None) -> bool:
    """True, wenn Kraken die Kursquelle ist (oder der Broker außerhalb des Papierbetriebs): dann sind Symbole Kryptopaare."""
    e = einst or {}
    return e.get("kursquelle") == "kraken" or (e.get("broker") == "kraken" and e.get("modus", "papier") != "papier")


# ---------------------------------------------------------------- Eingabe erkennen

def _kompakt(text: str) -> str:
    return re.sub(r"[\s._\-:/]", "", text).upper()


def _ticker(teil: str) -> str:
    """Name oder Ticker -> Ticker (Bitcoin, XBT, BTC -> BTC); Unbekanntes bleibt (Großbuchstaben)."""
    k = _kompakt(teil)
    return _NAMEN.get(k, KANON_AUS_KRAKEN.get(k, k))


def zerlegen(eingabe: str, standard: str = STANDARD_GEGENWAEHRUNG) -> Zerlegt:
    """Zerlegt eine Eingabe in Basis und Gegenwährung. Wirft SymbolFehler bei leerer oder unzulässiger Eingabe."""
    roh = (eingabe or "").strip()
    if not roh:
        raise SymbolFehler("Kein Symbol angegeben. Beispiele: BTC, Bitcoin, ETH/EUR, SOL.")
    if len(roh) > MAX_LAENGE or not ERLAUBT.match(roh):
        raise SymbolFehler(f"„{roh[:30]}“ ist kein gültiges Symbol (höchstens {MAX_LAENGE} Zeichen: Buchstaben, Ziffern und . - : / Leerzeichen).")
    standard = standard.upper()
    kompakt = _kompakt(roh)
    if kompakt in _NAMEN and kompakt not in FIAT_NAMEN:                 # ganzer Name, auch „Bitcoin Cash“
        return Zerlegt(_NAMEN[kompakt], standard, False, kompakt in _EINDEUTIG)
    teile = [t for t in re.split(r"[\s/\-_:]+", roh.upper()) if t]
    if len(teile) > 2:
        raise SymbolFehler(f"„{roh}“ habe ich nicht verstanden. Erwartet wird ein Wert (BTC) oder ein Paar (BTC/EUR).")
    if len(teile) == 2:
        basis, gegen = _ticker(teile[0]), _ticker(teile[1])
        bekannt = basis in COINS and (gegen in GEGENWAEHRUNGEN)
        return Zerlegt(basis, gegen, True, bekannt)
    tok = teile[0]
    if tok in _NAMEN or tok in KANON_AUS_KRAKEN:                        # BTC, XBT, ETH ...
        return Zerlegt(_ticker(tok), standard, False, tok in _EINDEUTIG)
    lang = _KRAKEN_LANG.fullmatch(tok)                                  # XXBTZEUR
    if lang and _ticker(lang.group(1)) in COINS and lang.group(2) in GEGENWAEHRUNGEN:
        return Zerlegt(_ticker(lang.group(1)), lang.group(2), True, True)
    for q in GEGENWAEHRUNGEN:                                           # BTCEUR, XBTEUR, ETHUSDT ...
        if tok.endswith(q) and len(tok) - len(q) >= 2:
            basis = _ticker(tok[:-len(q)])
            return Zerlegt(basis, q, True, basis in COINS)
    return Zerlegt(tok, standard, False, False)


def kanonisch(eingabe: str, einst: dict | None = None, *, krypto: bool | None = None) -> str:
    """Eingabe -> Symbol in der Form `BASIS/GEGEN` (BTC/EUR). Ohne Netz, es wird nicht geprüft, ob Kraken das Paar führt.

    Ist Kraken weder Kursquelle noch Broker (`krypto=False`), bleiben Werte unverändert (`aapl` -> `AAPL`), außer die Eingabe ist
    eindeutig eine Kryptowährung (`Bitcoin`, `BTC/EUR`, `XBTEUR`)."""
    krypto = nutzt_kraken(einst) if krypto is None else krypto
    z = zerlegen(eingabe, gegenwaehrung(einst))
    if krypto or z.eindeutig:
        return z.symbol
    pur = eingabe.strip().upper()
    if not _PUR.fullmatch(pur):
        raise SymbolFehler(f"„{eingabe.strip()[:30]}“ ist kein gültiges Symbol (Buchstaben, Ziffern und . - : / , ohne Leerzeichen).")
    return pur


def kanonisch_liste(eingaben, einst: dict | None = None, *, krypto: bool | None = None) -> list[str]:
    """Wie `kanonisch` für mehrere Eingaben; Doppelte fallen weg (BTC und Bitcoin sind dasselbe). Reihenfolge bleibt."""
    erg: list[str] = []
    for e in eingaben:
        s = kanonisch(e, einst, krypto=krypto)
        if s not in erg:
            erg.append(s)
    return erg


def kraken_name(eingabe: str, standard: str = STANDARD_GEGENWAEHRUNG) -> str:
    """Eingabe -> vermuteter Altname des Kraken-Paars (BTC -> XBTEUR, Bitcoin -> XBTEUR, DOGE/USD -> XDGUSD). Ohne Netz."""
    z = zerlegen(eingabe, standard)
    return KRAKEN_NAMEN.get(z.basis, z.basis) + z.gegen


def klartext(ticker: str) -> str:
    """Ausgeschriebener Name (BTC -> Bitcoin); unbekannte Ticker bleiben."""
    return COINS[ticker][0] if ticker in COINS else ticker


# ---------------------------------------------------------------- Kraken-Paarliste

@dataclass(frozen=True)
class KrakenPaar:
    schluessel: str             # XXBTZEUR
    altname: str                # XBTEUR
    wsname: str                 # XBT/EUR
    basis: str                  # BTC
    gegen: str                  # EUR

    @property
    def symbol(self) -> str:
        return f"{self.basis}/{self.gegen}"


def _asset_sauber(asset: str) -> str:
    """ZEUR -> EUR, XXBT -> XBT, XETH -> ETH; sonst unverändert (SOL, USDT)."""
    return asset[1:] if len(asset) == 4 and asset[0] in "XZ" else asset


def paar_aus_info(schluessel: str, info: dict) -> KrakenPaar | None:
    """Ein Eintrag von `/0/public/AssetPairs` -> KrakenPaar (None bei unbrauchbaren Einträgen und Dark-Pool-Paaren mit Punkt)."""
    if "." in schluessel or not isinstance(info, dict):
        return None
    ws = str(info.get("wsname") or "")
    if "/" in ws:
        roh_b, roh_q = ws.split("/", 1)
    else:
        roh_b, roh_q = _asset_sauber(str(info.get("base") or "")), _asset_sauber(str(info.get("quote") or ""))
    if not roh_b or not roh_q:
        return None
    return KrakenPaar(schluessel, str(info.get("altname") or schluessel), ws or f"{roh_b}/{roh_q}",
                      KANON_AUS_KRAKEN.get(roh_b, roh_b), KANON_AUS_KRAKEN.get(roh_q, roh_q))


def kraken_holen(basis_url: str = BASIS_URL, timeout: float = 20.0) -> dict:
    """Alle Handelspaare von Kraken (öffentlich, ein Abruf). Wirft SymbolFehler, wenn Kraken nicht erreichbar ist."""
    url = f"{basis_url.rstrip('/')}/0/public/AssetPairs"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "kitrader-symbole/0.1"}), timeout=timeout) as a:
            antwort = json.loads(a.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AbrufFehler(f"Die Paarliste von Kraken ließ sich nicht abrufen ({getattr(e, 'reason', e)}).") from None
    fehler = [str(x) for x in (antwort.get("error") or []) if str(x).startswith("E")]
    if fehler or not isinstance(antwort.get("result"), dict):
        raise AbrufFehler("Kraken lieferte keine Paarliste: " + ("; ".join(fehler) or "leere Antwort"))
    return antwort["result"]


class PaarVerzeichnis:
    """Die Handelspaare von Kraken, zum Nachschlagen und für Vorschläge.

    `holen()` liefert das `result` von AssetPairs (Standard: öffentlicher Abruf). `datei`: Zwischenspeicher auf der Platte (ein Tag gültig);
    ohne Datei gilt nur der Arbeitsspeicher. Ist Kraken nicht erreichbar, hilft eine ältere gespeicherte Liste."""

    def __init__(self, holen: Callable[[], dict] | None = None, datei: Path | str | None = None, uhr: Callable[[], float] = time.time):
        self.holen = holen or kraken_holen
        self.datei = Path(datei) if datei else None
        self.uhr = uhr
        self._paare: list[KrakenPaar] | None = None
        self._index: dict[tuple[str, str], KrakenPaar] = {}

    # ---- Laden

    def _setzen(self, paare: list[KrakenPaar]) -> None:
        self._paare = paare
        self._index = {}
        for p in paare:
            self._index.setdefault((p.basis, p.gegen), p)

    def _lesen(self, jung: bool) -> list[KrakenPaar] | None:
        if self.datei is None:
            return None
        try:
            d = json.loads(self.datei.read_text(encoding="utf-8"))
            if jung and self.uhr() - float(d["zeit"]) > GUELTIG_S:
                return None
            return [KrakenPaar(*z) for z in d["paare"]] or None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def laden(self, neu: bool = False) -> list[KrakenPaar]:
        if self._paare is not None and not neu:
            return self._paare
        if not neu:
            gespeichert = self._lesen(jung=True)
            if gespeichert:
                self._setzen(gespeichert)
                return gespeichert
        try:
            roh = self.holen()
        except Exception as e:
            alt = self._lesen(jung=False)
            if alt:
                self._setzen(alt)
                return alt
            raise e if isinstance(e, AbrufFehler) else AbrufFehler(f"Die Paarliste von Kraken ließ sich nicht abrufen ({e}).") from None
        paare = [p for k, i in (roh or {}).items() if (p := paar_aus_info(k, i))]
        if not paare:
            raise SymbolFehler("Kraken lieferte keine Handelspaare.")
        self._setzen(paare)
        if self.datei is not None:
            try:
                self.datei.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.datei.with_name(self.datei.name + ".tmp")
                tmp.write_text(json.dumps({"zeit": self.uhr(), "paare": [[p.schluessel, p.altname, p.wsname, p.basis, p.gegen] for p in paare]}),
                               encoding="utf-8")
                tmp.replace(self.datei)
            except OSError:
                pass                                                    # der Zwischenspeicher ist nur eine Hilfe
        return paare

    # ---- Suchen

    def finden(self, eingabe: str, standard: str = STANDARD_GEGENWAEHRUNG) -> KrakenPaar:
        """Das Kraken-Paar zur Eingabe. Wirft SymbolFehler mit Vorschlägen, wenn es keines gibt."""
        z = zerlegen(eingabe, standard)
        self.laden()
        treffer = self._index.get((z.basis, z.gegen))
        if treffer is None:
            ganz = _kompakt(eingabe)                                    # z. B. „STETH“ nicht als ST + ETH lesen
            treffer = self._index.get((_ticker(ganz), standard.upper()))
        if treffer is not None:
            return treffer
        raise SymbolFehler(self.fehlertext(eingabe, z))

    def basen(self) -> set[str]:
        return {p.basis for p in self.laden()}

    def moegliche(self, basis: str) -> list[str]:
        return sorted(p.symbol for p in self.laden() if p.basis == basis)

    def vorschlaege(self, eingabe: str, standard: str = STANDARD_GEGENWAEHRUNG, n: int = 3) -> list[str]:
        return vorschlaege(eingabe, standard, n, self._paare and self.basen())

    def fehlertext(self, eingabe: str, z: Zerlegt) -> str:
        roh = eingabe.strip()
        moeglich = self.moegliche(z.basis)
        if moeglich:
            return (f"Kraken führt {klartext(z.basis)} ({z.basis}) nicht gegen {z.gegen}. Möglich sind: {', '.join(moeglich[:8])}. "
                    f"Mit Gegenwährung eintippen, z. B. {moeglich[0]}.")
        v = self.vorschlaege(roh, z.gegen)
        return (f"Kraken kennt „{roh}“ nicht." + (f" Meintest du {' oder '.join(v)}?" if v else " Beispiele: BTC, Bitcoin, ETH/EUR, SOL."))


def vorschlaege(eingabe: str, standard: str = STANDARD_GEGENWAEHRUNG, n: int = 3, basen: set[str] | None = None) -> list[str]:
    """Ähnliche bekannte Namen als Symbole („Bitcon“ -> BTC/EUR). `basen`: zusätzlich die Basiswerte aus der Kraken-Paarliste."""
    wort = _kompakt(eingabe)
    kandidaten = dict(_NAMEN)
    for b in basen or ():
        kandidaten.setdefault(b, b)
    gut = sorted(((difflib.SequenceMatcher(None, wort, name).ratio(), name) for name in kandidaten), reverse=True)
    gut = [(r, name) for r, name in gut if r >= 0.72]
    erg: list[str] = []
    for r, name in gut:
        s = f"{kandidaten[name]}/{standard.upper()}"
        if r >= gut[0][0] - 0.12 and kandidaten[name] not in FIAT and s not in erg:
            erg.append(s)
    return erg[:n]
