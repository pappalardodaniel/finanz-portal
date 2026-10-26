# kitrader

Eine **private** Software für Daytrading, mit der ich Handelsideen erst auf dem Papier ausprobiere, bevor
überhaupt echtes Geld ins Spiel kommt. Sie ist nichts für andere Leute und kein fertiges Produkt.

> **Keine Anlageberatung, kein Erfolgsversprechen.** Die mitgelieferten Strategien sind Lehrbeispiele; die
> meisten einfachen Handelsregeln verlieren nach Kosten Geld. Ein guter Backtest beweist nichts über die
> Zukunft. Reihenfolge, die ich einhalte: **erst Backtest, dann mehrere Wochen Papierhandel, dann – wenn
> überhaupt – echtes Geld mit kleinen Grenzen**, und nur Geld, dessen Verlust ich verkraften kann.
> Wer das benutzt, ist für alles selbst verantwortlich.

## kitrader – Start

**Das Markt-Dashboard ist die Hauptoberfläche.** Ein Befehl startet es zusammen mit dem Autopiloten und dem Zeitplan und öffnet
den Browser: `python3 -m kitrader start` (oder Doppelklick auf `kitrader-starten.command`), danach http://127.0.0.1:8812/.

```bash
PYTHONPATH=src python3 -m kitrader start                 # Dashboard + Autopilot (Papier) + Zeitplan, Strg+C beendet alles
PYTHONPATH=src python3 -m kitrader.markt dashboard       # nur das Dashboard (der Autopilot startet im Reiter Autopilot)
PYTHONPATH=src python3 -m kitrader.markt demo            # Demo mit erfundenen Daten, http://127.0.0.1:8822/ (Strg+C beendet)
PYTHONPATH=src python3 -m kitrader.markt quellen         # alle Datenquellen mit ihren Nutzungsbedingungen
```

Reiter im Dashboard: **Kurse**, **Depot**, **kitrader Charts** (Indikatoren, Zeichenwerkzeuge, Papierhandel), **Trends** (Welt-Rotation,
Nachrichten-Radar, ähnliche Fälle früher, historische Ereignisse mit Ereignisstudie), **KI** (Einschätzung kurz/mittel/lang je Wert, letzte
Morgenlage, Trefferquote), **Weltlage** (OSIRIS), **Autopilot** (Betriebsart, Grenzen, Entscheidungen mit Gründen, Start/Stopp im Papierbetrieb,
roter **Not-Aus**), **Analysen**, **Orderbuch**, **Speicher**, **Datenquellen**. Die Handelsteile hängen unter `/api/k/…` (`markt/dashboard/kbruecke.py`);
schreibende Aufrufe brauchen den Kopf `X-Kitrader`. Die kleine Oberfläche (Port 8850, `python3 -m kitrader oberflaeche` oder `start --alte-oberflaeche`) ist nur noch optional.

- **Papierhandel in den Charts:** long und **Short**, bei jeder Eröffnung ein **Stop** (Pflicht) und ein optionales **Ziel**, Prüfung durch
  `kitrader.risiko` (Ablehnungsgründe stehen sofort in der Karte), „Position schließen“. Stop und Ziel überwacht der Kern, solange kitrader läuft.
- **Stop und Ziel immer im Bild:** Die Preisskala der Charts schließt Stop, Ziel und Einstand der offenen Position ein (solange die Skala automatisch ist).
- **Ereignisse im Chart:** Bei Tages- und Wochenkerzen (Zeiteinheit **1W** ist neu) zeigt der Schalter „Ereignisse“ Marker aus `kitrader.historie` (höchstens 60 im Bild,
  wichtige Arten zuerst; Form und Farbe je Gruppe). Hover: Titel, Datum, Quelle; Klick: Beschreibung und Ereignisstudie für den Wert (sonst BTC bzw. Dow).
  Bei anderen Zeiteinheiten auf Wunsch per Schalter. Endpunkte `/api/k/chart/ereignisse` und `/api/k/chart/studie` (nur lesend).
- **Ein Depot:** Der Reiter Depot zeigt oben die **Gesamtsumme** beider Papierkonten und beide Konten nebeneinander: „Manuell (Charts)“ und „Autopilot (Papier)“
  (Positionen, offene Orders, abgeschlossene Trades, Ergebnis; nur Anzeige, Endpunkt `/api/k/depot`). Die Konten selbst bleiben getrennt.
- **KI-Modell:** `ki.claude_modell` (Standard `sonnet`, leer = Standardmodell der CLI) wird der Claude-CLI als `--model` übergeben.
- **Symbole:** `BTC`, `Bitcoin`, `XBT`, `BTCEUR`, `BTC-EUR`, `BTC/EUR` … ergeben überall `BTC/EUR` (`src/kitrader/symbole.py`; Kraken-Paarliste
  zwischengespeichert; Tippfehler bekommen Vorschläge).
- **Ordner getrennt:** Marktdaten in `~/.local/share/kitrader/markt` (anders per `KITRADER_MARKT_DATEN`), Konfiguration und Schlüssel des Markt-Teils in
  `~/.config/kitrader/markt` (`KITRADER_MARKT_KONFIG`); Papierkonto, Journal und Historie der Handelsteile in `KITRADER_DATEN`.
- **KI-Agent (Node.js):** `agent/` (Claude als Chat im Dashboard, liest auch Trends, Weltlage, KI-Einschätzung und Autopilot-Status). Er startet nur,
  wenn Node.js da ist und `cd agent && npm install` gelaufen ist; ohne ihn läuft alles andere.
- **Tests:** `PYTHONPATH=src python3 -m unittest discover -s tests/markt -t .` (Markt-Teil),
  `node tests/js/test_indikatoren.cjs` (Indikatoren gegen Referenzwerte), `PYTHONPATH=src python3 -m unittest discover -s tests/kitrader -t .`
  (Handelsteile).
- **Dokumente zum Markt-Teil:** `docs/markt/` (Indikatoren, Datenqualität, Einrichtung, Fahrplan). Lizenzen und fremde
  Bestandteile: [LIZENZEN.md](LIZENZEN.md). Die Marktdaten selbst unterliegen den Bedingungen der jeweiligen Quelle.

Darunter folgen die Abschnitte zu den Handelsteilen (Broker, Risiko, Autopilot, Journal, Historie).

## Was es kann

- **Handeln in drei Betriebsarten:** *Papier* (Simulation mit echten Kursen, kein Geld), *Demo* (Demokonto
  beim Broker) und *Echtgeld* (nur mit doppelter Freigabe). Angebunden sind **Interactive Brokers** und
  **Kraken**.
- **Sicherungen vor jeder Order:** Risikoprüfung mit Grenzen für Verlust pro Tag, Positionsgröße,
  Handelszeiten und veraltete Kurse, dazu ein Not-Aus.
- **Backtest:** eine Strategie auf alten Kerzen testen, mit Gebühren und Schlupf und einem zurückgehaltenen
  Testteil.
- **Journal:** jede Entscheidung und jede eigene Einschätzung wird festgehalten und später ausgewertet
  (Trefferquote).
- **Lange Historie und Ereignisliste:** Kursreihen und Zinsen teils bis ins 18. Jahrhundert, Bitcoin ab 2010,
  Gold, Silber und Platin monatlich ab 1960, dazu über 300 Ereignisse (Krisen, Zinsschocks, Rezessionen)
  und eine Ereignisstudie: Was tat eine Reihe nach so einem Ereignis?
- **KI-Einschätzung und OSIRIS:** eine KI-Einschätzung der Lage und die Weltlage aus OSIRIS
  (osirisai.live) als Zusatzinformation. Das ist eine Meinung zum Nachdenken, kein Handelssignal, und beides
  ist noch in Arbeit.
- **Bedienoberfläche im Browser:** das Markt-Dashboard (nur auf diesem Rechner) mit großer Anzeige der Betriebsart im Reiter Autopilot
  und einem roten **Not-Aus-Knopf**. Von Hand gehandelt wird nur auf dem Papier (Reiter kitrader Charts); echte Orders setzt allein der Autopilot.
- **Alles in einem Befehl:** `start` (oder Doppelklick auf `kitrader-starten.command`) lässt Dashboard, Autopilot,
  Telegram-Wache und Zeitplan in einem Prozess laufen (dazu der Analysen-Dienst und, wenn Node da ist, der Claude-Agent); ein abgestürzter Teil wird neu gestartet, ohne die anderen mitzureißen.
- **Telegram:** Meldungen aufs Handy (Start/Stopp, Ausführungen, Ablehnungen, Tagesverlustgrenze, Tagesabschluss)
  und Not-Aus per Nachricht „stop“, aber nur aus dem eigenen Chat.

Nur Python-Standardbibliothek, ab Python 3.11, nichts zu installieren.

## Schnellstart: ein Befehl startet alles

**Auf dem Mac:** im Finder auf **`kitrader-starten.command`** doppelklicken (öffnet das Terminal). Das startet im
**Papierbetrieb** (Simulation mit echten Kursen, kein echtes Geld) den Autopiloten, das Markt-Dashboard (öffnet sich im
Browser), die Telegram-Wache (wenn Telegram eingerichtet ist) und den Zeitplan (OSIRIS-Weltlage alle 6 Stunden, Nachrichten-Radar und Welt-Rotation alle 2 Stunden, Morgenlage täglich
um 08:00). Beenden mit **Strg+C** im Terminal-Fenster oder per Doppelklick auf `kitrader-stoppen.command`. Vorausgesetzt ist
Python 3.11 oder neuer (`python3 --version`); fehlt es, sagt das Fenster es klar, und es wird nichts installiert. Beim ersten
Doppelklick kann macOS „nicht geöffnet“ melden: Rechtsklick auf die Datei, „Öffnen“, noch einmal „Öffnen“.

**Oder im Terminal, im Ordner dieses Projekts:**

```bash
PYTHONPATH=src python3 -m kitrader start           # ALLES in einem Befehl (Papier); --modus papier|demo|echt, --port, --nicht-oeffnen, --beenden
```

Ohne eigene Einstellungsdatei nimmt `start` im Papierbetrieb Kraken-Kurse (öffentlich, kein Konto nötig), BTC/EUR und ETH/EUR
rund um die Uhr und Kraken-Gebühren (0,80 %); das steht beim Start ausdrücklich da. Im Dashboard zeigen die Reiter **kitrader Charts** und **Autopilot** Kerzen, eigene Trades und Entscheidungen. Die mitgelieferten Strategien verlieren nach Kosten Geld
(`STRATEGIE-BERICHT.md`): Der Papierhandel ist ein Technik-Test.

Weitere Befehle einzeln:

```bash
PYTHONPATH=src python3 -m kitrader --help          # alle Befehle
PYTHONPATH=src python3 -m kitrader konto           # Papierkonto anzeigen
PYTHONPATH=src python3 -m kitrader backtest --help # eine Strategie auf Kerzen testen
PYTHONPATH=src python3 -m kitrader daten kraken BTC/EUR --takt 5m 1h --tage 90   # echte Kerzen holen (öffentliche Kraken-API)
PYTHONPATH=src python3 -m kitrader labor           # Strategie-Labor: Kandidaten auf echten Kerzen, mit Halten und Zufall (siehe STRATEGIE-BERICHT.md)
PYTHONPATH=src python3 -m kitrader labor lang      # langfristig: Trend/Momentum/CAPE auf Jahrzehnten (siehe LANGFRIST-BERICHT.md)
PYTHONPATH=src python3 -m kitrader autopilot       # Papierhandel starten (Standard, kein Geld)
PYTHONPATH=src python3 -m kitrader oberflaeche     # kleine Bedienoberfläche (optional, http://127.0.0.1:8850/)
PYTHONPATH=src python3 -m kitrader einschaetzung BTC --treiber aus   # Symbol-Erkennung: auch Bitcoin, XBT/EUR, BTC-EUR
PYTHONPATH=src python3 -m kitrader telegram einrichten   # Telegram-Bot verbinden (Anleitung: src/kitrader/LIESMICH.md)

PYTHONPATH=src python3 -m kitrader.historie reihen # welche langen Reihen es gibt
PYTHONPATH=src python3 -m kitrader.historie laden  # Reihen aus dem Netz holen
PYTHONPATH=src python3 -m kitrader.historie pruefen
PYTHONPATH=src python3 -m kitrader.historie studie 1929-boersenkrach
```

Alternativ einmal `pip install -e .`, dann reicht `kitrader …` statt `PYTHONPATH=src python3 -m kitrader …`.

Einstellungen (Grenzen, Märkte, Betriebsart) stehen in `~/.config/kitrader/einstellungen.toml`; die Vorlage
liegt in `src/kitrader/einstellungen.beispiel.toml`. Alle Daten (Papierkonto, Journal, Historie) liegen im
Ordner `KITRADER_DATEN`, Standard `~/.local/share/kitrader`, nie im Repo.

## Sicherungen

- **Papier ist der Standard.** Echtgeld startet nur, wenn in den Einstellungen `modus = "echt"` steht
  **und** im Datenordner eine Datei `ECHTGELD_FREIGABE` mit dem heutigen Datum liegt. Sie gilt nur für
  diesen Tag.
- **Risikoprüfung** für jede Order, egal woher sie kommt; alle verletzten Regeln werden gemeldet.
- **Not-Aus:** `python3 -m kitrader notaus` storniert die Orders und schließt die Positionen der **eigenen** Symbole
  (`autopilot.symbole` und alles, was der Autopilot eröffnet hat) und sperrt den Handel; fremde Positionen werden nur
  gemeldet. `notaus --alles` stellt das ganze Konto glatt. Dasselbe wie `notaus` macht der rote Knopf im Dashboard (Reiter Autopilot)
  und das Wort „stop“ im eigenen Telegram-Chat (mit laufender Wache). Aufheben geht nur am Rechner (`notaus --aufheben`).
  Rat: ein eigenes Konto oder Unterkonto nur für kitrader.
- **Tagesverlust ist eine harte Grenze:** bei Erreichen (realisiert plus unrealisiert) stellt der Autopilot alle eigenen
  Positionen glatt und sperrt neue bis zum nächsten Tag.
- **Zugangsdaten gehören nie ins Repo.** API-Schlüssel liegen in privaten Dateien unter `~/.config/kitrader/`;
  `*.env` (auch `telegram.env` mit dem Bot-Token), Datenbanken und Datenordner sind in `.gitignore` ausgenommen.
- **Oberfläche nur lokal:** das Dashboard bindet an 127.0.0.1 (andere Adressen nur ausdrücklich mit `--host`); schreibende Aufrufe der Handelsteile
  brauchen den Kopf `X-Kitrader`, passenden `Host` und `Origin`, damit fremde Webseiten nichts auslösen können (die kleine Oberfläche zusätzlich einen zufälligen Schlüssel).
- **Marktdaten gehören nicht ins Repo.** Die Historie lädt sie bei Bedarf von den Quellen und beachtet deren
  Bedingungen (Übersicht in `src/kitrader/historie/HISTORIE.md`, zum Beispiel ist Bitcoin von Coin Metrics
  nur nicht kommerziell nutzbar).

## Stand

Läuft und ist getestet (Tests unter `tests/kitrader`): Papier-Broker, Risikoprüfung, Strategien, Backtest,
Journal, Autopilot, Anbindung an Interactive Brokers und Kraken, Historie mit Ereignisstudie. Die
Broker-Anbindungen sind gegen Attrappen getestet, nicht gegen echte Konten; erst ausprobieren, wenn man
Demokonten eingerichtet hat. Offen: eine Strategie, die nach Kosten wirklich etwas taugt, und der Nachweis
im Papierhandel über mehrere Wochen.

Tests ausführen:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/kitrader -t .
```

## Weiterlesen

- `STRATEGIE-BERICHT.md` – Strategie-Labor: fünf Kandidaten plus die zwei Beispiele auf echten Kraken-Kursen, nach Kosten, mit Vergleich zu Halten und Zufall
- `src/kitrader/LIESMICH.md` – Betriebsarten, Risikoregeln, Einstellungen, Autopilot
- `src/kitrader/broker/BROKER.md` – Demokonto bei Interactive Brokers und Kraken-Zugang einrichten
- `src/kitrader/historie/HISTORIE.md` – Reihen, Quellen und ihre Bedingungen, Ereignisliste, Studie

Privates Projekt, keine Lizenz zur Weitergabe.
