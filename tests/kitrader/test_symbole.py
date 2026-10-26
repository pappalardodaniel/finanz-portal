"""Symbol-Erkennung (symbole.py): BTC, Bitcoin, XBT, BTCEUR, BTC-EUR ... -> BTC/EUR, Kraken-Paarliste, Vorschläge."""
import json
import tempfile
import unittest
from pathlib import Path

from kitrader import symbole

PAARE = {
    "XXBTZEUR": {"altname": "XBTEUR", "wsname": "XBT/EUR", "base": "XXBT", "quote": "ZEUR"},
    "XXBTZUSD": {"altname": "XBTUSD", "wsname": "XBT/USD", "base": "XXBT", "quote": "ZUSD"},
    "XETHZEUR": {"altname": "ETHEUR", "wsname": "ETH/EUR", "base": "XETH", "quote": "ZEUR"},
    "SOLEUR": {"altname": "SOLEUR", "wsname": "SOL/EUR", "base": "SOL", "quote": "ZEUR"},
    "XXDGXXBT": {"altname": "XDGXBT", "wsname": "XDG/XBT", "base": "XXDG", "quote": "XXBT"},
    "XBTEUR.d": {"altname": "XBTEUR.d", "wsname": "XBT/EUR.d", "base": "XXBT", "quote": "ZEUR"},
}
KRAKEN = {"kursquelle": "kraken", "symbole": {"gegenwaehrung": "EUR"}}
OHNE = {"kursquelle": "ibkr", "symbole": {"gegenwaehrung": "EUR"}}


class KanonischTest(unittest.TestCase):
    def test_schreibweisen_von_bitcoin(self):
        for eingabe in ("BTC", "btc", "Bitcoin", "XBT", "BTCEUR", "BTC-EUR", "BTC/EUR", "xbt/eur", "XBTEUR", "XXBTZEUR", "BTC EUR", "Bitcoin/EUR"):
            self.assertEqual(symbole.kanonisch(eingabe, KRAKEN), "BTC/EUR", eingabe)

    def test_andere_werte(self):
        self.assertEqual(symbole.kanonisch("Ethereum", KRAKEN), "ETH/EUR")
        self.assertEqual(symbole.kanonisch("ether", KRAKEN), "ETH/EUR")
        self.assertEqual(symbole.kanonisch("SOL", KRAKEN), "SOL/EUR")
        self.assertEqual(symbole.kanonisch("Solana", KRAKEN), "SOL/EUR")
        self.assertEqual(symbole.kanonisch("DOGE", KRAKEN), "DOGE/EUR")
        self.assertEqual(symbole.kanonisch("XDGUSD", KRAKEN), "DOGE/USD")
        self.assertEqual(symbole.kanonisch("ETHUSDT", KRAKEN), "ETH/USDT")

    def test_gegenwaehrung_aus_den_einstellungen(self):
        self.assertEqual(symbole.kanonisch("BTC", {**KRAKEN, "symbole": {"gegenwaehrung": "usd"}}), "BTC/USD")
        self.assertEqual(symbole.kanonisch("BTC/EUR", {**KRAKEN, "symbole": {"gegenwaehrung": "USD"}}), "BTC/EUR")

    def test_aktien_bleiben_ohne_kraken(self):
        self.assertEqual(symbole.kanonisch("aapl", OHNE), "AAPL")
        self.assertEqual(symbole.kanonisch("BRK.B", OHNE), "BRK.B")
        self.assertEqual(symbole.kanonisch("Bitcoin", OHNE), "BTC/EUR")      # Name einer Kryptowährung ist eindeutig

    def test_ungueltiges(self):
        for eingabe in ("", "   ", "a" * 40, "BTC;DROP", "<b>"):
            with self.assertRaises(symbole.SymbolFehler, msg=eingabe):
                symbole.kanonisch(eingabe, KRAKEN)

    def test_liste_ohne_doppelte(self):
        self.assertEqual(symbole.kanonisch_liste(["BTC", "Bitcoin", "XBT/EUR", "ETH"], KRAKEN), ["BTC/EUR", "ETH/EUR"])

    def test_kraken_name(self):
        self.assertEqual(symbole.kraken_name("Bitcoin"), "XBTEUR")
        self.assertEqual(symbole.kraken_name("BTC/USD"), "XBTUSD")
        self.assertEqual(symbole.kraken_name("DOGE"), "XDGEUR")
        self.assertEqual(symbole.kraken_name("SOL"), "SOLEUR")


class VerzeichnisTest(unittest.TestCase):
    def setUp(self):
        self.aufrufe = 0
        self.verz = symbole.PaarVerzeichnis(holen=self._holen)

    def _holen(self):
        self.aufrufe += 1
        return PAARE

    def test_findet_und_merkt_sich_die_liste(self):
        self.assertEqual(self.verz.finden("Bitcoin").altname, "XBTEUR")
        self.assertEqual(self.verz.finden("XBT/USD").schluessel, "XXBTZUSD")
        self.assertEqual(self.verz.finden("SOL").symbol, "SOL/EUR")
        self.assertEqual(self.aufrufe, 1)                                  # ein Abruf genügt, Dark-Pool-Paare (mit Punkt) zählen nicht

    def test_unbekanntes_paar_nennt_moegliche(self):
        with self.assertRaises(symbole.SymbolFehler) as ctx:
            self.verz.finden("BTC/CHF")
        self.assertIn("BTC/EUR", str(ctx.exception))

    def test_tippfehler_macht_vorschlaege(self):
        with self.assertRaises(symbole.SymbolFehler) as ctx:
            self.verz.finden("Etherum")
        self.assertIn("ETH/EUR", str(ctx.exception))

    def test_zwischenspeicher_auf_der_platte(self):
        with tempfile.TemporaryDirectory() as t:
            datei = Path(t) / "paare.json"
            uhr = [1000.0]
            a = symbole.PaarVerzeichnis(holen=self._holen, datei=datei, uhr=lambda: uhr[0])
            a.finden("BTC")
            self.assertTrue(json.loads(datei.read_text())["paare"])
            b = symbole.PaarVerzeichnis(holen=lambda: self.fail("neu geholt"), datei=datei, uhr=lambda: uhr[0] + 60)
            self.assertEqual(b.finden("ETH").altname, "ETHEUR")
            # abgelaufen und Kraken nicht erreichbar: die alte Liste hilft
            uhr[0] += 3 * 24 * 3600

            def kaputt():
                raise symbole.AbrufFehler("Netz weg")
            c = symbole.PaarVerzeichnis(holen=kaputt, datei=datei, uhr=lambda: uhr[0])
            self.assertEqual(c.finden("ETH").altname, "ETHEUR")

    def test_ohne_netz_und_ohne_liste_ein_abruffehler(self):
        def kaputt():
            raise OSError("kein Netz")
        with self.assertRaises(symbole.AbrufFehler):
            symbole.PaarVerzeichnis(holen=kaputt).finden("BTC")


if __name__ == "__main__":
    unittest.main()
