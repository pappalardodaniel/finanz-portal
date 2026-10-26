# kitrader – private KI-Trading-Software

kitrader handelt Aktien oder Krypto automatisch (Daytrading), aber **zuerst nur auf dem Papier**: mit echten
Kursen, aber ohne echtes Geld. Es ist ein Werkzeug zum Ausprobieren und Prüfen, kein Gelddruckautomat.

> **Kein Erfolgsversprechen.** Die beiden mitgelieferten Strategien sind Lehrbeispiele. Die meisten
> einfachen Handelsregeln verlieren nach Kosten Geld. Vorgehen: **erst Backtest (mit zurückgehaltenem
> Testteil), dann mindestens mehrere Wochen Papierhandel, dann – wenn überhaupt – echtes Geld nur mit
> kleinen Grenzen** und nur Geld, dessen Verlust du verkraftest. Ein guter Backtest beweist nichts über die
> Zukunft. Du bist selbst für alles verantwortlich, was kitrader in deinem Namen tut.

## Alles starten

**Ein Befehl startet alles:**

```
python3 -m kitrader start [--modus papier|demo|echt] [--port 8812] [--host 127.0.0.1] [--nicht-oeffnen] [--symbole A B] [--strategie NAME]
                          [--ohne-hilfsdienste] [--alte-oberflaeche]   # Symbole auch kurz: BTC, Bitcoin, XBT/EUR (siehe symbole.py)
python3 -m kitrader start --beenden       # den laufenden Start sauber beenden (wie Strg+C im Fenster; auch aus einem zweiten Terminal)
```

Auf dem Mac genügt ein **Doppelklick auf `kitrader-starten.command`** im Repo-Ordner (öffnet das Terminal, prüft Python ab 3.11 und
installiert nie etwas; fehlt Python oder ist es zu alt, steht die Meldung im Fenster). Dasselbe im **Papierbetrieb** startet die Datei, und
`kitrader-stoppen.command` beendet es wieder. Beim ersten Mal meldet macOS evtl., die Datei sei von einem nicht verifizierten Entwickler:
Rechtsklick, „Öffnen“.

`start` läuft in **einem Prozess** und startet diese Teile, jeden in einem eigenen Faden unter Aufsicht. **Hauptoberfläche ist das Markt-Dashboard**
(http://127.0.0.1:8812/, Reiter Trends, KI, Weltlage und Autopilot für die Teile unten; Papierhandel mit Short, Stop und Risikoprüfung in den Charts);
die kleine Oberfläche (Abschnitt „Bedienoberfläche im Browser“, Port 8850) gibt es nur noch auf Wunsch:

| Teil | Aufgabe |
|---|---|
| Autopilot | handelt wie `autopilot` (Standard **Papier**); alle Regeln unverändert. `echt` nur mit `modus = "echt"` in den Einstellungen **und** der Freigabedatei von heute, das wird **vor** allem anderen geprüft (sonst startet gar nichts); `demo` braucht wie immer das Demokonto |
| Oberfläche | die Bedienoberfläche auf 127.0.0.1 (Standard Port 8850; ist er belegt, der nächste freie bis +9; mit `--port` fest), öffnet sich im Browser |
| Telegram-Wache | „stop“ und „status“ aus dem eigenen Chat, nur wenn Telegram eingerichtet und nicht ausgeschaltet ist; läuft als eigener Teil, damit der Not-Aus per Handy auch geht, wenn der Autopilot ausgefallen ist |
| Zeitplan | OSIRIS-Weltlage alle `[zeitplan] osiris_alle_h` Stunden (Standard 6, 0 = aus; beim Start erst, wenn der letzte Schnappschuss so alt ist), **Nachrichten-Radar** (`nachrichten abrufen`) alle `nachrichten_alle_h` und **Welt-Rotation** (`welt`) alle `welt_alle_h` Stunden (je Standard 2, 0 = aus; gleiche Regeln: beim Start erst, wenn der gespeicherte Stand so alt ist; bei Fehlern Meldung und neuer Versuch nach 30 Minuten; fehlt das Modul, wird übersprungen und einmal gemeldet) und die **Morgenlage** täglich zu `[zeitplan] morgenlage` (Standard „08:00“, Ortszeit von `zeitzone`, Standard Europe/Berlin; „“ = aus). Wird `start` erst später am Tag gestartet und gibt es heute noch keine `morgenlage.json`, wird sie sofort nachgeholt. Die Morgenlage ruft `lage.ausfuehren` (siehe „Morgenlage“); geht die KI nicht, kommt die Lage ohne KI-Einschätzung; fehlt `lage.py`, wird übersprungen und einmal gemeldet; bei Fehlern bis zu 3 Versuche im Abstand von 30 Minuten |

**Aufsicht:** Stürzt ein Teil ab oder beendet er sich von selbst (z. B. nach 5 Fehlern in Folge, wenn das Netz weg ist), laufen die anderen weiter, und der Teil
wird nach einer Pause neu gestartet (10 s, 20 s, 40 s … höchstens 5 Minuten; war er mindestens 10 Minuten stabil, beginnt es wieder bei 10 s). Jeder Ausfall steht im
Terminal und im Journal (Ereignis `teil_absturz`), die Einzelheiten (Traceback) in `start-fehler.log` im Datenordner, und der erste, dritte, zehnte … Ausfall einer
Serie geht zusätzlich per Telegram aufs Handy. **Dauerhafte** Fehler (`Abbruch`: keine Symbole, Echtgeld-Freigabe abgelaufen, falsche Einstellung) führen nicht zu
Neustarts, sondern zu einer klaren Meldung. **Strg+C** (oder `start --beenden`, SIGTERM) stoppt alle Teile: der Autopilot beendet seinen laufenden Durchlauf, die Oberfläche
fährt herunter, ein Teil, der nach 30 Sekunden noch hängt, wird gemeldet und mit dem Programm beendet; ein zweites Strg+C beendet sofort. Offene Positionen behalten beim Broker ihre
Schutz-Stops; **im Papierbetrieb werden Stops und Ziele nur geprüft, solange kitrader läuft.**

**Doppelstart verhindern:** `start.lock` im Datenordner hält eine Dateisperre (flock); sie verschwindet mit dem Prozess, auch bei einem Absturz, es bleibt also keine „tote“
Sperre. Ein zweiter `start` im selben Datenordner wird mit Zeit, Prozessnummer und Adresse der Oberfläche verweigert (zwei Autopiloten würden Orders doppelt senden).

**Startausgabe:** Die Betriebsart steht groß da (PAPIER blau, DEMO gelb, ECHT rot mit Ausrufezeichen), dann die Adresse der Oberfläche, **was läuft** und **was fehlt**
(Telegram nicht eingerichtet, `lage.py` nicht vorhanden, Claude-CLI nicht gefunden, Kursquelle nicht erreichbar), jeweils mit dem Befehl zum Beheben.

**Voreinstellungen ohne eigene Einstellungsdatei (nur Papierbetrieb):** Damit der Doppelklick sofort Live-Kurse zeigt, setzt `start` Werte, die du **nicht selbst** eingestellt hast
(die Einstellungsdatei hat Vorrang, Wert für Wert): Kursquelle `kraken` (öffentliche Kurse, kein Konto nötig; Standard sonst `ibkr`, das ein laufendes Gateway braucht), `autopilot.symbole`
= `[watchlist] werte` (BTC/EUR, ETH/EUR), für diese Symbole Markt `krypto` (rund um die Uhr) und Mengenschritt (BTC 0,0001, ETH 0,001, sonst 0,01) und, wenn **kein** `[kosten]`
eingestellt ist, die Kraken-Pro-Gebühren (0,80 % je Ausführung, keine feste Gebühr, siehe `STRATEGIE-BERICHT.md`), damit der Papierhandel nicht schöner aussieht als die Wirklichkeit.
Das alles steht beim Start unter „Vorgaben“. In `demo` und `echt` setzt `start` **nichts** voreingestellt.

## Was kitrader kann

| Baustein | Datei | Aufgabe |
|---|---|---|
| Papier-Broker | `broker/papier.py` | Simulation: Markt-, Limit-, Stop-, Stop-Limit-Orders, Stop-Loss/Take-Profit als OCO, Short, Gebühren, FIFO-Ergebnis je Trade, Zustand in `papier.db` |
| Risikoprüfung | `risiko.py` | harte Regeln, die **jede** Order bestehen muss, egal von wem sie kommt |
| Strategien | `strategie/` | Schnittstelle plus zwei Beispiele (Eröffnungs-Ausbruch, Rückkehr zum VWAP) und fünf **Kandidaten** (`strategie/kandidaten.py`: Donchian, Bollinger/RSI, gleitende Durchschnitte, Momentum, Volumen-Ausbruch; ungeprüft), Positionsgröße aus dem Risiko |
| Backtest | `backtest.py` | Test auf Kerzen mit Gebühren (Maker/Taker getrennt), Spread, Schlupf; gleiche Risikoprüfung wie live; Walk-forward |
| Kerzen holen | `daten.py` | echte Kerzen aus der öffentlichen Kraken-API (über die einzelnen Abschlüsse, auch für Monate), als CSV im Datenordner |
| Strategie-Labor | `labor.py` | Kandidaten auf echten Kerzen: Walk-forward, Vergleich mit Kaufen und Halten und mit Zufall; Ergebnis in `STRATEGIE-BERICHT.md` |
| Labor langfristig | `labor_lang.py`, `strategie/langfrist.py` | Trend, Momentum, CAPE auf Tages-/Monatsreihen über Jahrzehnte (Dow, S&P, Bitcoin, Gold, UK): Kosten, Walk-forward, Zufall, Ereignisse; Befehl `labor lang`; Ergebnis in `LANGFRIST-BERICHT.md` |
| Journal | `journal.py` | `journal.db`: jede Entscheidung; Einschätzungen mit automatischer Auswertung und Trefferquote |
| Autopilot | `autopilot.py` | Schleife: Kerzen holen, Strategie, Risikoprüfung, Broker, Journal |
| Broker-Anbindungen | `broker/ibkr.py`, `broker/kraken.py` | Interactive Brokers (Demo/Echt) und Kraken; siehe `broker/BROKER.md` |
| Weltlage | `osiris.py` | holt offene Live-Feeds von OSIRIS (osirisai.live) und fasst sie als Fakten zusammen |
| KI-Einschätzung | `ki.py` | KI schätzt kurz (1 Tag), mittel (3 Monate), lang (1 Jahr) ein; Ergebnis ins Journal; sendet nie Orders |
| Vorwärtstest | `vorwaertstest.py` | KI sagt 15 und 60 Minuten voraus, Papier-Trades mit echten Kursen, Auswertung gegen Zufall mit Fehleralarm, Lernen aus Nachbesprechungen; Befehl `python3 -m kitrader.vorwaertstest` |
| Alles starten | `start.py`, `kitrader-starten.command` | ein Befehl: Autopilot, Oberfläche, Telegram-Wache, Zeitplan (OSIRIS, Nachrichten, Welt, Morgenlage) in einem Prozess, mit Aufsicht, Neustart und Sperrdatei |
| Bedienoberfläche | `oberflaeche/` | Seite im Browser, nur auf diesem Rechner: Übersicht (mit Morgenlage), Live (Kerzenchart mit Trades), Journal, Weltlage, Historie, KI und ein großer Not-Aus-Knopf; sendet nie Orders |
| Telegram | `telegram.py` | Meldungen aufs Handy; Not-Aus per Nachricht „stop“ aus deinem Chat |
| Gemeinsames | `betrieb.py` | Broker öffnen, Übersicht und Not-Aus – von Oberfläche und Telegram gemeinsam genutzt |

Nur Python-Standardbibliothek (ab Python 3.11).

## Betriebsarten

| Betriebsart | Geld | Was passiert |
|---|---|---|
| `papier` (Standard) | keins | Papier-Broker mit echten Kursen (Kursquelle IBKR oder Kraken im Nur-Lese-Betrieb) |
| `demo` | keins | Demokonto des echten Brokers (IBKR-Konto „DU…“; Kraken: nur „prüfen“, Orders werden validiert, nicht ausgeführt) |
| `echt` | **echtes Geld** | nur mit doppelter Freigabe (siehe unten) |

**Freigabe für `echt`.** Der Autopilot startet im Echtgeld-Betrieb nur, wenn **beides** stimmt:

1. in `einstellungen.toml` steht `modus = "echt"`, **und**
2. im Datenordner liegt die Datei `ECHTGELD_FREIGABE`, die das **heutige Datum** (`JJJJ-MM-TT`, z. B. `2026-10-25`)
   enthält.

Sonst bricht kitrader mit einer klaren Meldung ab. Die Freigabe wird bei jedem Durchlauf neu geprüft: löschst du
die Datei oder wird es ein neuer Tag, beendet sich der Autopilot (offene Positionen behalten ihre Schutz-Stops
beim Broker). Außerdem prüft kitrader, dass der Broker zur Betriebsart passt (kein Demokonto im Echt-Betrieb,
kein Echtgeld-Konto im Demo-Betrieb).

Beim Start zeigt kitrader ein deutliches Banner mit Betriebsart und Grenzen; im Echtgeld-Betrieb mit
Ausrufezeichen und „ECHTES GELD“.

## Sicherungen

**Risikoprüfung** (`risiko.pruefen(order, lage)` → `Ergebnis(ok, gruende)`). Alle Grenzen stehen in den
Einstellungen (Abschnitt `[risiko]`), die Standardwerte sind vorsichtig. Es werden **alle** verletzten Regeln
gemeldet.

| Regel | Standard | Wirkung |
|---|---|---|
| Not-Aus | – | Datei `NOTAUS` im Datenordner oder Flag: **alles** blockiert |
| Veralteter Kurs | älter als 60 s | kein Handel (auch kein Schließen) |
| Kurszeit unbekannt | Quelle nennt keine echte Zeit (IBKR ohne `_updated`; Kraken ohne Handelsdaten) | keine **Eröffnung** (Schließen bleibt erlaubt) |
| Handelszeiten je Markt | US 09:30–16:00 New York, Xetra 09:00–17:30 Berlin, Krypto 24/7 | außerhalb kein Handel; Markt je Symbol über `[maerkte]` |
| Keine Eröffnung kurz vor Schluss | letzte 15 Min. | nur Eröffnungen |
| Tagesverlustgrenze | 100 absolut **und** 1 % vom Gesamtwert (realisiert plus unrealisiert) | **harte Grenze:** keine neuen Positionen bis zum nächsten Tag (bleibt gesperrt, auch wenn der Verlust zwischendurch kleiner wird) **und** der Autopilot stellt alle eigenen Positionen glatt (`tagesverlust_schliessen`, Standard an) |
| Maximale Positionsgröße | 10 % vom Gesamtwert | zählt offene, noch nicht ausgeführte Eröffnungsorders im selben Symbol mit |
| Maximale offene Positionen | 3 | zählt Symbole mit offener Eröffnungsorder mit; sind die offenen Orders nicht lesbar, wird nicht eröffnet |
| Maximale Orders je Tag | 20 | |
| Stop-Loss-Pflicht | an | jede Eröffnung braucht einen Stop-Loss auf der richtigen Seite |
| Risiko je Trade | 0,5 % vom Gesamtwert | (Einstieg − Stop) × Menge, einschließlich schon bestellter Eröffnungsmenge |
| Währung | – | Kurse in fremder Währung werden mit dem Wechselkurs in die Kontowährung umgerechnet (Größe, Risiko); gibt es keinen Kurs (Broker oder Einstellung `wechselkurse`), keine Eröffnung |
| Short | verboten | nur mit `short_erlaubt = true` |

Als „Eröffnung“ gilt, was die Position betragsmäßig vergrößert oder umdreht. Reine Verkleinerungen (Schließen)
sind von den Eröffnungsregeln ausgenommen, damit eine Sperre nie verhindert, dass ein Risiko abgebaut wird.
Not-Aus, veraltete Kurse und Handelszeiten gelten aber auch fürs Schließen.

**Weitere Schutzmaßnahmen**

- **Eigene Symbole.** Schutz-Check und Not-Aus fassen nur **eigene** Symbole an: `autopilot.symbole` (bzw. `--symbole`)
  plus alles, was der Autopilot selbst eröffnet hat (das merkt sich das Journal, auch über einen Neustart; der Eintrag
  verschwindet, wenn die Position nach mehreren Durchläufen in Folge fehlt). **Fremde Positionen** (z. B. von Hand im
  selben Konto gekauft) werden nur gemeldet (einmal je Lauf, Journal und Telegram), nie angefasst und nicht auf einen
  Schutz-Stop geprüft. **Rat: ein eigenes Konto oder Unterkonto nur für kitrader** (IBKR: Unterkonto bzw. eigenes
  Demokonto; Kraken: eigener Unterkonto-Schlüssel). Dann gibt es nichts Fremdes, und die Frage stellt sich nicht.
- **Schutz-Stops prüfen:** Der Autopilot kontrolliert bei jedem Durchlauf, dass jede **eigene** Position einen Stop-Order beim
  Broker hat. Fehlt er, wird die Position geschlossen (Einstellung `autopilot.ohne_schutz`: `schliessen` oder `warnen`).
- **Not-Aus:** `python3 -m kitrader notaus` legt die Datei `NOTAUS` an und storniert die Orders der **eigenen Symbole** und stellt
  deren Positionen zum Markt glatt; Fremdes wird aufgelistet („fremd, nicht angefasst“). **`python3 -m kitrader notaus --alles`**
  stellt dagegen das **ganze Konto** glatt (mit deutlicher Warnung) – das nur, wenn wirklich alles zu muss. Ein laufender
  Autopilot sieht die Datei beim nächsten Durchlauf, macht dasselbe (nur eigene Symbole) und handelt erst wieder, wenn die
  Datei gelöscht ist (`python3 -m kitrader notaus --aufheben`). Klappt das Glattstellen nicht, wird es bei jedem Durchlauf
  neu versucht. Der Not-Aus-Knopf der Oberfläche und „stop“ per Telegram verhalten sich wie `notaus` ohne `--alles`.
- **Tagesverlust als harte Grenze:** Der Autopilot prüft die Grenze in **jedem** Durchlauf (nicht nur bei neuen Kerzen),
  gemessen am Gesamtwert, also realisiert plus unrealisiert. Bei Erreichen sperrt er den Tag (Tageswechsel nach UTC) und stellt
  alle eigenen Positionen glatt; schlägt das fehl, wird es in jedem Durchlauf erneut versucht. Fremde Positionen bleiben
  unberührt. Abschaltbar mit `[risiko] tagesverlust_schliessen = false` (dann nur Sperre, offene Positionen behalten ihren Stop).
- **Gewinnziel selbst überwachen:** Führt der Broker das Ziel nicht als Order (Kraken hängt nur den Stop an), merkt sich der
  Autopilot das Ziel je Position im Journal (übersteht einen Neustart). Erreicht der Kurs das Ziel (Long: Bid ≥ Ziel,
  Short: Ask ≤ Ziel), storniert er den Stop und stellt glatt (durch die Risikoprüfung); Meldung `ziel_erreicht`. IBKR und der
  Papier-Broker führen das Ziel selbst als Order, dort wird nichts doppelt überwacht.
- **Daytrading:** Vor Handelsschluss (`autopilot.tagesabschluss_min_vor_schluss`) wird alles glattgestellt
  (nicht bei Märkten ohne Schluss wie Krypto).
- **Fehler:** Nach `autopilot.fehler_max` Fehlern in Folge (Verbindung, Kursquelle …) beendet sich der Autopilot.
- **Einmal pro Kerze:** Die Strategie sieht nur abgeschlossene Kerzen und wird je Kerze einmal gefragt.

## Befehle

```
python3 -m kitrader konto                       Kontostand (Betriebsart aus den Einstellungen oder --modus)
python3 -m kitrader positionen                  offene Positionen
python3 -m kitrader orders [--alle]             offene (oder alle) Orders
python3 -m kitrader backtest STRATEGIE CSV      Backtest mit zurückgehaltenem Testteil
        [--markt us|xetra|krypto] [--symbol S] [--ohne-teilung] [--train-anteil 0.7] [--faltungen N] [--json]
python3 -m kitrader daten kraken PAAR [--takt 5m 15m 1h] [--tage N] [--pause S] [--max-abrufe N] [--neu]
        echte Kerzen der öffentlichen Kraken-API holen (z. B. BTC/EUR) und als CSV in <Datenordner>/kerzen/ ablegen
python3 -m kitrader labor [--paare BTC/EUR ETH/EUR] [--takte 5m 15m 1h] [--strategien NAME …] [--faltungen 4] [--zufall 200]
python3 -m kitrader labor lang [--reihen sp dow dow_div btc gold uk] [--zufall 500] [--kosten-aktie 0.1] [--kosten-krypto 0.5] [--markdown MD]   # langfristig, Daten aus kitrader.historie
        [--taker 0.80 --maker 0.40 --spread 0.05 --schlupf 0.02] [--ausgabe JSON] [--markdown MD] [--aus-json JSON]
        Strategie-Labor auf den geholten Kerzen (siehe unten und STRATEGIE-BERICHT.md)
python3 -m kitrader papier [zuruecksetzen]      Papierkonto, Trades (FIFO); „zuruecksetzen“ beginnt neu
python3 -m kitrader autopilot [--modus papier|demo|echt] [--symbole A B] [--strategie NAME]
        [--schritte N] [--wiedergabe CSV --markt krypto]
python3 -m kitrader notaus [--alles|--aufheben]   Not-Aus setzen (nur eigene Symbole; --alles = ganzes Konto) / aufheben
python3 -m kitrader journal [--trefferquote] [--anzahl 20]
python3 -m kitrader einschaetzung AAPL steigt 1d "Begründung" [--preis 231.5]   eigene Einschätzung festhalten
python3 -m kitrader einschaetzung AAPL [--treiber claude_cli|anthropic|aus] [--kerzen CSV] [--kontext]
        KI schätzt kurz/mittel/lang ein (ohne --kerzen: Kerzen von der Kursquelle, --takt 1d --anzahl 250)
python3 -m kitrader morgenlage [--watchlist BTC/EUR,ETH/EUR] [--treiber claude_cli|anthropic|aus] [--ohne-telegram] [--ohne-datei]
        Morgenlage: KI-Einschätzung kurz/mittel/lang je Wert, OSIRIS-Veränderungen, KI-Trefferquote (Text, Telegram, morgenlage.json)
python3 -m kitrader osiris abrufen [--erzwingen]   OSIRIS-Feeds holen und als Schnappschuss speichern
python3 -m kitrader osiris lage [--json]           letzte Weltlage samt Veränderung zum vorigen Schnappschuss
python3 -m kitrader nachrichten abrufen [--erzwingen] [--ohne-gdelt]   weltweite Feeds und GDELT holen (siehe „Nachrichten-Radar“)
python3 -m kitrader nachrichten radar [--json]       Radar der letzten 24 Stunden (Themen, Ereignisarten, Märkte, Stimmung)
python3 -m kitrader nachrichten suche "TEXT" [--limit N]   gespeicherte Meldungen durchsuchen
python3 -m kitrader welt [--ohne-abruf] [--erzwingen] [--json]   Welt-Rotation: Gewinner und Verlierer nach Region und Anlageklasse (siehe „Welt-Rotation“)
python3 -m kitrader start [--modus M] [--port P] [--nicht-oeffnen] [--beenden]   ALLES starten (siehe „Alles starten“)
python3 -m kitrader oberflaeche [--port 8850] [--nicht-oeffnen]   nur die Bedienoberfläche im Browser starten
python3 -m kitrader telegram einrichten [--neu]    Telegram einrichten (Token, Chat-ID, Testnachricht)
python3 -m kitrader telegram test                  Testnachricht schicken
python3 -m kitrader telegram wache [--modus M]     auf „stop“ / „status“ aus deinem Chat hören (Not-Aus per Telegram)
```

Alles mit `PYTHONPATH=src` vor dem Befehl, wenn kitrader nicht installiert ist.

**Backtest-CSV:** Spalten `t,o,h,l,c,v` (Zeit, Eröffnung, Hoch, Tief, Schluss, Umsatz), Kopfzeile erlaubt,
`t` als ISO-Zeit (`2026-02-02T14:30:00Z`) oder Unix-Sekunden. Zeiten in UTC.

**Ausprobieren ohne Netz:** `autopilot --wiedergabe daten.csv --markt krypto` spielt Kerzen aus einer Datei ab
(eigener Unterordner `wiedergabe/` im Datenordner, das echte Papierkonto bleibt unberührt).

## Einstellungen

Datei `~/.config/kitrader/einstellungen.toml` (anderer Pfad über `KITRADER_KONFIG`). Vorlage mit allen Werten
und Erklärungen: `src/kitrader/einstellungen.beispiel.toml`. Die Datei muss nur enthalten, was du ändern willst.
Daten (`papier.db`, `journal.db`, `NOTAUS`, `ECHTGELD_FREIGABE`) liegen in `KITRADER_DATEN`
(Standard `~/.local/share/kitrader`). **API-Schlüssel gehören nie ins Repo**, nur in deine private Einstellungsdatei
bzw. (Kraken) in `~/.config/kitrader/kraken.env`, (Telegram) in `~/.config/kitrader/telegram.env`.

Wichtigste Abschnitte: `modus`, `broker`, `kursquelle`, `[papier]` (Startguthaben), `[kosten]`, `[risiko]` (auch
`tagesverlust_schliessen`), `[wechselkurse]` (Reserve-Kurse, z. B. `USD = 0.92` bei Kontowährung EUR: Wert einer Einheit in
Kontowährung; der Kurs vom Broker geht vor), `[maerkte]` und `[handelszeiten.*]`, `[strategie.*]`, `[backtest]`, `[journal.schwellen]`, `[autopilot]`,
`[osiris]` (Adresse, Pausen), `[ki]` (Treiber, Modell, Filter), `[oberflaeche]` (Port, `live_takt`, `live_kerzen`), `[zeitplan]` (für `start`: `morgenlage`, `zeitzone`,
`osiris_alle_h`, `nachrichten_alle_h`, `welt_alle_h`) und `[telegram]` (Meldungen, Wache).

## Backtest und Walk-forward

Der Backtest läuft Kerze für Kerze und schaut nie in die Zukunft: Ein Signal am Ende einer Kerze wird zum
Eröffnungskurs der nächsten ausgeführt. Berühren Stop und Ziel dieselbe Kerze, gilt der Stop. Springt der Kurs
über den Stop, wird zum schlechteren Kurs ausgeführt. Kosten: feste Gebühr + Prozentgebühr je Ausführung, halber
Spread je Seite, Schlupf bei Markt-Orders und Stops. Das Ziel ist eine Limit-Order und füllt erst, wenn der Kurs
es um den halben Spread durchläuft. Am Tagesende wird glattgestellt.

**Zurückgehaltener Datenteil:** Die Daten werden zeitlich geteilt (Standard 70 % Training / 30 % Test, immer an
einem Tagesanfang). Die Strategie-Parameter werden **nur auf dem Trainingsteil** ausgesucht (Gitter der
Strategie, bestes Netto-Ergebnis bei mindestens `min_trades_training` Trades). Der Testteil wird danach einmal
mit diesen Parametern gerechnet und **getrennt** berichtet. Nur die Testspalte sagt etwas aus; das Training ist
durch das Einstellen geschönt. Mit `--faltungen N` wird der Testteil in N Abschnitte geteilt, jeder mit den
Parametern, die auf allen früheren Daten gewählt wurden.

**Kennzahlen:** Netto-Ergebnis (nach Kosten), Ergebnis vor Kosten, Gebühren, Spread + Schlupf, Anzahl Trades,
Trefferquote, Profitfaktor, durchschnittlicher Gewinn/Verlust, maximaler Rückgang (nach jedem abgeschlossenen
Trade), Sharpe (aus Tagesrenditen, annualisiert mit `tage_pro_jahr`). Bei weniger als 30 Trades weist die
Ausgabe darauf hin, dass die Zahlen kaum aussagekräftig sind.

## Echte Kerzen holen und das Strategie-Labor

**Kerzen holen:** `python3 -m kitrader daten kraken BTC/EUR --takt 5m 15m 1h --tage 90` schreibt je Takt eine Datei
`<Datenordner>/kerzen/kraken_XBTEUR_5m.csv` usw. (`t,o,h,l,c,v`, UTC, direkt für `backtest` und `labor` lesbar). Krakens
Kerzen-Abfrage (`OHLC`) liefert nur die letzten 720 Kerzen je Takt (bei 5 Minuten gut zweieinhalb Tage). Für längere Zeiträume
holt kitrader die einzelnen Abschlüsse (`Trades`, 1000 je Abruf, seitenweise) und verdichtet sie selbst zu Kerzen; ein Vergleich
mit Krakens eigenen Kerzen stimmte bei 41 von 42 Kerzen exakt. **Das sind viele Abrufe:** BTC/EUR hatte im Herbst 2026 je nach Tag etwa 10.000 bis 40.000 Abschlüsse (90 Tage: 1,6 Millionen =
1.621 Abrufe, bei der eingestellten Höflichkeit von 1 s Pause plus Antwortzeit gut eine halbe Stunde), ETH/EUR etwa 0,9 Millionen (900 Abrufe).
Es gibt eine Pause zwischen den Abrufen, bei Ratenbegrenzung längere Wartezeiten, `--max-abrufe`
als Obergrenze und einen Zwischenstand (derselbe Befehl setzt nach einem Abbruch fort, `--neu` beginnt von vorn). Zeiträume ohne
Abschluss ergeben keine Kerze (Lücken werden gemeldet), die laufende Kerze wird verworfen. Die Daten sind nur für den privaten
Gebrauch (Bedingungen von Kraken beachten) und gehören nie ins Repo.

**Labor:** `python3 -m kitrader labor` rechnet für jeden Kandidaten, jedes Paar und jeden Takt ein Walk-forward mit mehreren
Faltungen (Parameter nur aus früheren Daten, berichtet wird nur über die Testteile), vergleicht mit Kaufen und Halten und mit
200 Zufallsläufen gleicher Trade-Anzahl, Haltedauer, Größe und Kosten, und macht bei weniger als 30 Test-Trades keine Aussage. Standard
sind die Kraken-Pro-Gebühren der niedrigsten Stufe (Taker 0,80 %, Maker 0,40 %, Stand 26.10.2026; `[kosten] gebuehr_prozent`
ist die Taker-Gebühr für Marktorders und Stops, der optionale Wert `gebuehr_maker_prozent` gilt für das Gewinnziel als Limit-Order).
Das Labor rechnet immer als Daytrading (am UTC-Tagesende glatt), nur Long, mit den Standardgrenzen von kitrader (die persönliche Einstellungsdatei wird bewusst nicht gelesen, damit Ergebnisse wiederholbar bleiben).
Ergebnisse und Einordnung: `STRATEGIE-BERICHT.md` im Repo.

## Papierhandel – was die Simulation nicht abbildet

Der Papier-Broker ist bewusst einfach: Marktorders laufen zum Ask/Bid plus Schlupf; Limit-Orders füllen, sobald
der Kurs das Limit erreicht (in Wirklichkeit steht man in einer Warteschlange und füllt oft nicht); es gibt keine
Teilausführungen, keine Marktwirkung großer Orders, keine Dividenden, Steuern, Finanzierungskosten oder Leerverkaufsgebühren.
Kurse in fremder Währung (z. B. USD-Aktien bei Kontowährung EUR) rechnet nur die Risikoprüfung um; das Papierkonto selbst
bucht sie 1:1. Ohne Wechselkurs (Kursquelle oder `[wechselkurse]`) wird in fremder Währung nicht eröffnet.
Echte Ergebnisse werden schlechter ausfallen. Mehrere Wochen Papierhandel zeigen vor allem, ob die Technik
sauber läuft und ob die Strategie unter Marktbedingungen nicht sofort zerfällt.

## Journal und Einschätzungen

`journal.db` hält jede Entscheidung fest: Signal, Risikoprüfung (mit Gründen), Order, Ausführung und Ergebnis, jeweils
mit Zeit (`python3 -m kitrader journal`).

**Einschätzungen** sind Aussagen wie „AAPL steigt in 1d“ (Wert, Richtung `steigt`/`faellt`/`seitwaerts`, Horizont
`1h 4h 1d 1w 1M 3M 1J`, Begründung, Kurs bei Abgabe). Nach Ablauf des Horizonts wertet der Autopilot sie
automatisch gegen den echten Kurs aus. Treffer: `steigt` = Plus von mindestens der Schwelle, `faellt` = Minus von
mindestens der Schwelle, `seitwaerts` = Bewegung bleibt unter der Schwelle (Schwellen je Horizont unter
`[journal.schwellen]`). `journal --trefferquote` zeigt die Trefferquote je Horizont, **immer mit n**. Mit wenigen
Fällen ist die Quote Zufall; erst ab etwa 30 Fällen je Horizont lohnt es, sie ernst zu nehmen. Aus Python:
`Journal().einschaetzung("AAPL", "steigt", "1d", "Grund", 231.5)`.

## Weltlage aus OSIRIS

[OSIRIS](https://osirisai.live) (Open Source, MIT, selbst hostbar) bündelt offene Live-Feeds. kitrader nutzt davon die für
Märkte brauchbaren, alle ohne Schlüssel (Doku: osirisai.live/docs, „API Reference“): `/api/conflicts`, `/api/gdelt`,
`/api/country-risk` (Geopolitical), `/api/news`, `/api/markets`, `/api/scm-suppliers` (Media & Markets), `/api/maritime`
(Häfen und Engpässe wie Hormus), `/api/earthquakes`, `/api/weather`, `/api/radar` (Earth & Environment),
`/api/cyber-threats` (Cyber), `/api/stats` (System).

`osiris abrufen` holt die Feeds nacheinander mit Pause (`osiris.pause_s`), mit ehrlichem User-Agent und höchstens alle
`osiris.mindestabstand_s` Sekunden, und legt einen Schnappschuss in `osiris.db` (Datenordner) ab; Schnappschüsse älter
als `osiris.behalten_tage` werden gelöscht. Fällt ein Feed aus, steht das im Schnappschuss, die anderen bleiben.
`osiris lage` fasst den letzten Schnappschuss zusammen: Vorfälle nach Art und Gebiet, Konfliktzonen, Länderrisiko,
wichtigste Meldungen mit Zeit und Quelle, Engpässe, Märkte, Erdbeben, Cyber, und was sich seit dem vorigen
Schnappschuss geändert hat. Es sind nur Fakten aus den Feeds; Stufen wie „HIGH“ oder „Score“ sind OSIRIS-eigen und so
gekennzeichnet.

**Grenzen, die man kennen muss:** `/api/gdelt` liefert auf osirisai.live derzeit GDACS-Katastrophenmeldungen, keine
GDELT-Konfliktereignisse (die Ausgabe nennt die Quelle). Die Konfliktzonen sind eine feste Liste von OSIRIS. Der
Meldungs-Score zählt nur Schlüsselwörter. Die Telegram-Kanäle im Meldungsfeed haben eine eigene Ausrichtung (Feld
„Lager“). Das Gebiet der Vorfälle ist eine grobe Einteilung nach Koordinaten. Für Selbst-Hosting `[osiris] adresse` ändern.

## KI-Einschätzung

`python3 -m kitrader einschaetzung AAPL` baut einen Kontext und fragt die KI nach `kurz` (1d), `mittel` (3M) und `lang`
(1J), je mit Richtung (`steigt`/`faellt`/`seitwaerts`), Wahrscheinlichkeit, Begründung, Gegenargumenten und
`was_widerlegt` (woran man erkennt, dass sie falsch liegt). Kontext: Kerzen mit Kennzahlen (Trend, Schwankung, Abstand
zu Hoch/Tief), der **Muster-Abschnitt** der Muster-Engine (`historie/muster.py`, siehe `historie/HISTORIE.md`; von Hand
mit `python3 -m kitrader.historie muster`, `aehnlich` und `zusammenhang`): die ähnlichsten früheren Ereignisse mit Begründung
(Ähnlichkeitssuche), ihre Reaktion in mehreren Märkten (Dow, DAX, Bitcoin, Gold, Öl, VIX, Zins) gegen normale Zeiträume und der
Krypto-Aktien-Zusammenhang, immer mit n und höchstens 3.000 Zeichen; die Ereignisarten folgen festen Regeln aus der OSIRIS-Lage
(z. B. Konfliktzone „war“ gleich Ereignisart `krieg`). Dazu kommen die OSIRIS-Lage und die bisherige Trefferquote der
KI-Einschätzungen. Die Einstellung `ki.historie_reihe` (früher: US-Aktienreihe für die Ereignisstudien) **wirkt auf den
Muster-Abschnitt nicht mehr**; sie gilt nur noch für den älteren Aufbau der Ereignisstudie (Ersatzobjekte, Tests). Mit `--kontext` oder `--treiber aus` siehst du genau, was die KI bekommt.

Die Antwort muss strenges JSON sein und wird geprüft; bei Fehlern wird einmal nachgefragt, sonst gibt es einen Fehler.
Jede gültige Einschätzung kommt ins Journal (Horizonte 1d, 3M, 1J; Begründung mit Marke `[KI]`, Wahrscheinlichkeit,
Gegenargumente und Widerlegung im Text), die Horizonte laufen ab dem Ende der letzten Kerze. Der Autopilot wertet sie
nach Ablauf automatisch aus (`journal --trefferquote`). Die Trefferquote der KI wird erst ab etwa 30 Fällen je
Horizont aussagekräftig; mehrere Einschätzungen zum selben Wert kurz nacheinander sind nicht unabhängig.

**Treiber** (`[ki] treiber`):

| Treiber | Wie |
|---|---|
| `claude_cli` (Standard) | ruft die lokal installierte Claude-CLI auf (`claude -p … --output-format json --tools ""`), nutzt dein Abo; Werkzeuge sind abgeschaltet, weil der Prompt fremde Texte (Meldungen) enthält; Zeitlimit `ki.zeitlimit_s` |
| `anthropic` | Messages API (`ki.modell`, Standard `claude-sonnet-5-5`); Schlüssel aus `ANTHROPIC_API_KEY` oder `~/.config/kitrader/anthropic.env` (Zeile `ANTHROPIC_API_KEY=sk-ant-…`, `chmod 600`, nie ins Repo) |
| `aus` | kein Aufruf, zeigt nur den Kontext |

**Die KI sendet nie Orders.** Im Autopiloten kann sie nur ein optionaler Filter sein (`[ki] filter = true`, Standard
`false`): Ein Eröffnungssignal gegen die kurzfristige KI-Richtung (1d, mindestens `filter_min_wahrscheinlichkeit`)
wird verworfen und im Journal vermerkt. Schließen geht immer durch. Ist die KI nicht erreichbar, wird nicht eröffnet.
Die KI wird je Symbol höchstens alle `filter_max_alter_h` Stunden gefragt (das kann pro Antwort Minuten dauern, der
Autopilot wartet währenddessen). Die Risikoprüfung gilt danach unverändert für jede Order.

**Ehrlich gesagt:** Eine KI, die Text liest, hat keinen Informationsvorsprung vor dem Markt. Die Einschätzungen sind
Denkanstöße zum Gegenprüfen, keine Prognose; ob sie besser als Raten sind, zeigt erst die Trefferquote nach vielen Fällen.

## KI handelt selbst (Strategie „ki“)

Die Strategie `ki` (`strategie/ki_strategie.py`) lässt die KI-Einschätzung (`ki.py`) den Autopiloten steuern: **long** bei „steigt“,
**short** bei „faellt“ (nur wenn `risiko.short_erlaubt = true`), sonst nichts. Die KI sendet auch hier nie Orders; sie liefert ein Signal,
und dieses geht wie bei jeder Strategie durch `signal_zu_order` und die **Risikoprüfung** (Pflicht-Stop, Positionsgröße, Tagesverlust,
Handelszeiten, Kursalter, Not-Aus). Die Risikoprüfung bleibt vor jeder Order, auch wenn die KI sich sicher ist.

**Einschalten (Papierbetrieb zuerst):** in `~/.config/kitrader/einstellungen.toml`

```toml
[autopilot]
symbole = ["BTC/EUR"]        # ein Symbol reicht für den Anfang
takt = "5m"                  # Daytrading; für horizont = "mittel": takt = "1d"
[strategie]
name = "ki"
[strategie.ki]
horizont = "kurz"            # "kurz" = 1-Tages-Einschätzung, "mittel" = 3 Monate (Swing)
abstand_min = 120            # nur alle 2 Stunden die KI fragen (Standard: 30 Minuten, bei Tageskerzen 6 Stunden)
[risiko]
short_erlaubt = true         # nur wenn auch Short im Papierbetrieb laufen soll (Standard false)
[ki]
treiber = "claude_cli"       # dein Claude-Abo; "aus" geht hier nicht
```

Dann `python3 -m kitrader autopilot --modus papier` (oder `--strategie ki`). Ohne Netz ausprobieren: `autopilot --wiedergabe CSV`
(die KI wird dabei wirklich gefragt, siehe Kosten). Alle Parameter mit Standardwerten stehen in `einstellungen.beispiel.toml`
(Block `[strategie.ki]`).

**Was sie tut, in dieser Reihenfolge:**

1. Je Symbol wird die KI höchstens alle `abstand_min` Minuten gefragt (Zwischenspeicher; gerechnet wird mit der Zeit der Lage, im
   Backtest und in der Wiedergabe also mit der simulierten Zeit). Bei `horizont = "kurz"` werden Kerzen unter einer Stunde für die KI zu
   Stundenkerzen verdichtet (die 1-Tages-Einschätzung auf 5-Minuten-Rauschen wäre sinnlos); die ATR kommt aus denselben Kerzen.
   Gibt es noch keine 15 Stundenkerzen, wird die KI gar nicht erst gefragt. Jede Antwort landet im Journal (Marke `[KI]`, Trefferquote).
2. Die Richtung kommt aus dem gewählten Horizont (`kurz` = 1d, `mittel` = 3M). Die Wahrscheinlichkeit muss mindestens
   `min_wahrscheinlichkeit` (Standard 0,6) betragen. „seitwaerts“, zu wenig Wahrscheinlichkeit und Short bei `short_erlaubt = false`
   ergeben kein Signal; der Grund steht als Vermerk im Journal (Entscheidungen, Strategie `ki`).
3. **Kostenschwelle:** Stop = `stop_atr` mal ATR (Standard 1,5), Ziel = `ziel_atr` mal ATR (Standard 3) vom letzten Schluss. Der
   erwartete Weg (Abstand zum Ziel) muss die Kosten um den Faktor `kosten_faktor` (Standard 3) übersteigen. Kosten = Gebühren und
   Schlupf zweimal, Spread einmal und die feste Gebühr zweimal bezogen auf den Positionswert, den die Risikoregeln erlauben würden
   (`[kosten]` der Einstellungen). Außerdem muss die Erwartung nach Kosten positiv sein: `p · Ziel − (1 − p) · Stop − Kosten > 0`.
4. Jede Einschätzung wird genau einmal bewertet. Eine offene Position wird nie aufgestockt; dreht die KI mit Mindestwahrscheinlichkeit
   gegen sie, kommt „flat“ (`ausstieg_bei_umkehr`). Stop und Ziel gelten wie sonst; im Daytrading mit Börsen-Handelszeiten stellt der
   Autopilot vor Schluss glatt, Krypto läuft rund um die Uhr (Position bleibt, bis Stop, Ziel oder Umkehr).
5. KI nicht erreichbar, Antwort auch nach Nachfrage ungültig, Aufrufgrenze erreicht → **kein Signal** und ein Vermerk im Journal.
   Nach einem Fehler wird frühestens nach 10 Minuten neu gefragt.

**Kosten:** Jeder Aufruf ist eine Claude-CLI-Anfrage über dein Abo mit rund 15.000 Zeichen Kontext und dauert etwa 25 Sekunden bis eine
Minute (der Autopilot wartet währenddessen; Stops stehen beim Broker). Bei 30 Minuten Abstand sind das bis zu 48 Aufrufe je Tag und
Symbol rund um die Uhr und zählt gegen dein Abo-Kontingent. Deshalb die Bremsen `max_aufrufe_pro_tag` (Standard 0 = unbegrenzt) und `max_aufrufe`
(je Lauf); beide zählen echte Treiber-Aufrufe inklusive Nachfragen. Mit `treiber = "anthropic"` fallen API-Kosten an.

**Short:** Alle Strategien melden Long und Short (`ausbruch`, `vwap` und die Kandidaten haben beides). Ob Short gehandelt wird, entscheidet
allein `risiko.short_erlaubt` (Standard `false`). Der Papier-Broker erlaubt Short (`papier.short_erlaubt`), bei Kraken braucht es
`kraken.hebel` größer 0 (Margin), bei IBKR ein Konto mit Short-Erlaubnis.

**Ehrliche Grenzen:**

- **Kein Beleg, dass das Geld verdient.** Die einfachen Regeln im `STRATEGIE-BERICHT.md` verloren nach Kosten, und eine KI, die Text liest,
  hat keinen Informationsvorsprung vor dem Markt. Die Strategie ist ein Werkzeug zum Messen: Papierhandel mehrere Wochen, Journal und
  Trefferquote ansehen, erst bei vielen ausgewerteten Fällen (mindestens 30 je Horizont, besser 100) und Plus nach Kosten überhaupt über
  mehr nachdenken. Echtgeld nur mit der üblichen Freigabe und nie auf dieser Grundlage allein.
- **Kein Backtest mit echter KI.** Die KI kennt die Vergangenheit aus ihrem Training, ein Rückblick wäre Schummeln, dazu viele Aufrufe. Die
  Strategie verweigert deshalb den Backtest ohne eingesetzten Treiber; getestet wird nur mit einem Fake-Treiber (Tests). Die einzige
  ehrliche Prüfung ist der Vorwärtslauf im Papierhandel.
- **Die KI-Wahrscheinlichkeit ist nicht kalibriert.** „steigt mit 0,7“ heißt in ihrer Regel: nach 1 Tag mindestens 1 % höher
  (`journal.schwellen`), nicht „Ziel vor Stop“. Die Erwartungsrechnung nimmt trotzdem p für „Ziel vor Stop“ an. In ruhigen Phasen liegt die
  1-%-Schwelle nahe an der Tagesschwankung, dann ist „seitwaerts“ die wahrscheinlichste Aussage und es wird kaum gehandelt. Bei den
  echten Kraken-Kursen am 26.10.2026 war das so (siehe `UEBERGABE.md`). Die Schwelle zu senken ändert, was „Treffer“ heißt.
- **Kosten fressen den kleinen Weg.** Bei den Kraken-Pro-Gebühren (Taker 0,8 %) besteht praktisch kein Daytrading-Signal die Kostenschwelle.
  Das ist gewollt, aber dann passiert schlicht nichts, bis `kosten.*` zu deinem tatsächlichen Konto passt.
- In der Wiedergabe kennt die KI die OSIRIS-Lage von heute, nicht die von damals (OSIRIS speichert nur Schnappschüsse ab jetzt).
- Die Einschätzung hängt am Kontext (Kerzenkennzahlen, OSIRIS, Historie). Sind für die Muster-Engine keine Reihen geladen
  (`python3 -m kitrader.historie laden`), steht das im Muster-Abschnitt und die KI nennt es als Lücke; mehr Kontext ist keine Garantie für bessere Aussagen.

## Morgenlage

`python3 -m kitrader morgenlage` (Watchlist `[watchlist] werte`, Standard BTC/EUR und ETH/EUR, oder `--watchlist SAP,AAPL`) holt je Wert
Tageskerzen von der Kursquelle und fragt die KI (kurz/mittel/lang). Dazu kommen die **OSIRIS-Veränderungen** gegenüber dem vorigen Schnappschuss
(OSIRIS wird bei `osiris.automatisch` frisch geholt, wenn älter als `ki.osiris_max_alter_h`) und die **bisherige KI-Trefferquote** aus dem Journal.
Ausgabe: Text im Terminal, `morgenlage.json` im Datenordner (die Oberfläche liest sie; Aufbau im Kopf von `lage.py`) und, wenn Telegram
eingerichtet ist, eine auf Telegram-Länge gekürzte Fassung per `telegram.melden` (`--ohne-telegram`, `--ohne-datei` schalten das ab).
Je Wert ein KI-Aufruf (etwa 25 s, über dein Abo); mit `--treiber aus` kostet es nichts und zeigt Kurs, Veränderungen, OSIRIS und Trefferquote ohne
Einschätzung. Fehler bei einem Wert (Kursquelle, KI) stehen bei diesem Wert, die anderen laufen weiter. Aus Python: `lage.ausfuehren(einst, ordner)`
macht Erstellen, Speichern und Melden in einem Schritt (für Zeitpläne).

## Vorwärtstest (KI mit Live-Daten und Spielgeld)

**Zweck.** Die KI sieht den Kurs (1- und 5-Minuten-Kerzen von Kraken), die Weltlage (OSIRIS) und passende Muster aus der Historie und sagt für **15 und 60 Minuten**
voraus, ob der Kurs steigt, fällt oder seitwärts läuft, mit Wahrscheinlichkeit, Ziel, Stop, Hebel (1 bis 3, nur Spielgeld) und einer Begründung nach Faktoren
(Kurs, Historie, Weltlage). Zu jeder Vorhersage mit Richtung wird ein **Papier-Trade** mit echten Kursen eröffnet und bis Ziel, Stop oder Ablauf des Horizonts
geprüft. Danach wird gemessen, ob das stimmt: Trefferquote, Ergebnis nach Kosten, Vergleich mit Zufall. Kommt „100 % falsch“ heraus, gibt es einen Fehleralarm:
dann steckt sehr wahrscheinlich ein Fehler im Aufbau (Richtung vertauscht, Bid/Ask, Zeit), nicht in der KI. Code: `vorwaertstest.py`, eigenständig, nur Standardbibliothek.

**Warum nur vorwärts.** Die KI kennt die Vergangenheit (Kurse, Nachrichten, Ausgänge von Ereignissen). Ein Test auf alten Daten prüfte Gedächtnis statt Voraussicht.
Hier gibt es zum Zeitpunkt der Frage die Antwort des Marktes noch nicht. Der Preis dafür: Ergebnisse brauchen Zeit, und eine Marktphase gilt nur für sich.

```
python3 -m kitrader.vorwaertstest laufen --dauer 480 --takt 5            # 8 Stunden, alle 5 Minuten, BTC/EUR + ETH/EUR, Modell sonnet
python3 -m kitrader.vorwaertstest laufen --dauer 60 --modell opus --gebuehr 0.8 --symbole BTC/EUR
python3 -m kitrader.vorwaertstest auswerten                              # deutsche Tabellen + vorwaertstest_bericht.md im Datenordner
python3 -m kitrader.vorwaertstest selbsttest                             # Hellseher muss 100 % ergeben, Immer-falsch muss den Alarm auslösen
python3 -m kitrader.vorwaertstest pruefen                                # Technikprüfung der gespeicherten Daten
```

Daten: `vorwaertstest.db`, Protokoll `vorwaertstest.log`, Bericht `vorwaertstest_bericht.md` im Datenordner (`KITRADER_DATEN`). Wichtige Optionen von `laufen`:
`--dauer` (Minuten des Fragens, 0 = bis Strg+C), `--takt` (Minuten zwischen Runden), `--modell` (Standard `sonnet`, geht als `--model` an die Claude-CLI), `--gebuehr` (Prozent je Seite,
Standard 0,40 = Kraken Pro Taker; Option 0,8), `--einsatz` (Spielgeld je Trade, Standard 1000 EUR), `--schwelle-15/--schwelle-60` (Treffer-Schwellen 0,05 %/0,10 %),
`--nachlauf` (Minuten, die nach dem Ende auf offene Fälle gewartet wird; Standard bis alles abgerechnet ist, höchstens 63; 0 = sofort beenden, der Rest bleibt „offen“),
`--max-ki-aufrufe`, `--zeitlimit`, `--osiris-minuten` (OSIRIS höchstens alle 30 Minuten neu). Zum Lernen siehe unten. Strg+C (und SIGTERM) beenden sauber; Netz- und KI-Fehler
werden nur protokolliert, der Lauf geht weiter. Vor dem Lauf läuft der Selbsttest (unter 2 Sekunden); schlägt er fehl, startet nichts. Ein zweiter Lauf im selben Datenordner wird verweigert (Sperrdatei).

**Ablauf je Runde und Symbol.**
1. Echte Daten: Kerzen, Kurs mit echter Kurszeit (Zeit des letzten Handels), OSIRIS-Lage und Historie-Muster (beide zwischengespeichert, OSIRIS höchstens alle 30 Minuten neu).
2. Eine KI-Anfrage für beide Horizonte (`claude -p … --model sonnet --output-format json --tools ""`). Die Grundregeln des Prompts kommen aus `ki.REGELN`, nicht dupliziert.
   Die Antwort ist strenges JSON und wird auf Plausibilität geprüft (Ziel und Stop auf der richtigen Seite, Abstand mindestens 0,05 % und höchstens das Sechsfache der typischen
   Schwankung des Horizonts, Hebel 1 bis 3, Faktoren nicht leer); bei einem Fehler wird einmal nachgefragt, sonst wird die Runde für das Symbol übersprungen.
3. Nach der Antwort wird ein frischer echter Kurs geholt: das ist der **Einstieg** (Long: Ask, Short: Bid) und der Beginn des Horizonts. Alle Zeiten sind UTC. Liegen Ziel oder Stop
   dann schon auf der falschen Seite, wird der Trade verworfen (die Richtung zählt trotzdem).
4. Jede Minute (und genau zum Ablauf) wird der Kurs geholt: Long steigt zum Bid aus, Short zum Ask. **Ziel**: Ausstieg zum Zielkurs; **Stop**: Ausstieg zum beobachteten Kurs (Durchrutschen
   geht zu Lasten); **Ablauf**: Ausstieg zum Bid bzw. Ask. Gebühr je Seite auf den Wert der Seite; der Hebel wirkt auf das Ergebnis (Verlust höchstens −100 % des Einsatzes).
   Fehlt der Kurs länger als 5 Minuten oder kommt der Auswertungskurs mehr als 3 Minuten nach dem Ablauf, wird der Fall als „Lücke“ aus der Wertung genommen, nicht geschätzt.

**Auswertung** (`auswerten`): Trefferquote je Horizont (Treffer = Kurs, Mitte aus Bid und Ask, nach Ablauf in der vorhergesagten Richtung um mindestens die Schwelle; „seitwaerts“ = Änderung
unter der Schwelle) mit n und 95-%-Wilson-Bereich; dazu der **Richtungstreffer** (nur steigt/faellt bei Bewegung über der Schwelle; Raten ergibt dort etwa 50 %). Vergleich auf genau denselben Zeitpunkten:
**Zufallsrichtung** (die Richtungen der KI werden 2000-mal zufällig auf die Zeitpunkte verteilt), **immer Long**, **„die letzten 15/60 Minuten setzen sich fort“**. Dazu Verwechslungsmatrix,
**Kalibrierung** je Wahrscheinlichkeitsband, Trades (Anteil Ziel/Stop/Ablauf, Ergebnis vor und nach Gebühren, mit und ohne Hebel, Summe in EUR) und der Vergleich „bis Ablauf gehalten“
(ohne Ziel/Stop, damit nur die Richtung zählt). Oben steht ein **Kurzfazit** in wenigen ehrlichen Sätzen (unter 30 ausgewerteten Fällen je Horizont ausdrücklich „zu wenige für eine Aussage“). Ausgabe als Tabelle im Terminal und als `vorwaertstest_bericht.md`.

**Fehleralarm und Selbsttest.** „FEHLERVERDACHT: Richtung vertauscht?“ erscheint, wenn der Richtungstreffer signifikant unter 50 % liegt (Bereich ganz unter 0,5), auffällig nahe 0 % liegt oder die Trefferquote
schlechter ist als 97,5 % der Zufallsläufe; mit Prüfhilfen (Vorzeichen Long/Short, Bid/Ask, Zeitzonen, Symbol, Felder der KI) und einer Stichprobe zum Nachrechnen. Der eingebaute **Selbsttest**
lässt einen Hellseher (schaut in die Zukunft der Simulation) durch denselben Weg laufen wie die echte KI: er muss 100 % ergeben und vor Gebühren Gewinn machen; ein „Immer-falsch“-Fake muss 0 %, Verlust
und den Alarm ergeben. `auswerten` führt ihn jedes Mal aus. Die **Technikprüfung** (`pruefen`, auch automatisch beim Lernen) rechnet aus den gespeicherten Daten nach: Seite aus Richtung, Einstieg und Ausstieg zum richtigen
Kurs (Bid/Ask), das Ergebnis jedes Trades mit einer unabhängigen Gegenrechnung über Geldflüsse, Zeitzone und Reihenfolge der Zeiten, ob Kurs bei Abgabe und Auswertungskurs zum selben Symbol passen,
ob die KI Ziel und Stop richtig herum geliefert hat, und die Auswertung selbst.

**Lernen während des Laufs** (`--lernen an`, Standard). Alle `--lern-abstand` Minuten (Standard 15) und sobald mindestens `--lern-neu` (4) neue Vorhersagen ausgewertet sind:
1. **Fehlerprüfung zuerst.** Die Technikprüfung läuft immer; ist die Trefferquote nach mindestens 10 Vorhersagen sehr schlecht (Richtungstreffer-Bereich unter 50 % oder Trefferquote unter 25 %), steht das im Protokoll.
   Findet sie einen Technikfehler, wird das deutlich gemeldet und **weder gelernt noch angepasst**, bis er behoben ist.
2. **Feste Anpassungen ohne KI** (`--regeln an`, Standard; protokolliert in der Tabelle `anpassungen` und im Bericht), jeweils nur bei n ≥ 4 frischen Fällen (`--min-n-anpassung`, vorher 6) seit der letzten Änderung des Parameters:
   Mindest-Wahrscheinlichkeit anheben (0,55 / 0,60 / 0,65 / 0,70), wenn das neu auszusperrende Band im Mittel nach Kosten verliert; „erwartete Bewegung ≥ k × Kosten“ (k bis 2), wenn Trades mit zu kleiner
   Erwartung verlieren (Erwartung = p × Zielabstand − (1 − p) × Stopabstand); Hebel senken (3 → 2 → 1), wenn die Trades im Mittel verlieren; den schlechteren Horizont pausieren. **Rücknahme**, wenn die
   gesperrten „Schatten-Trades“ (sie laufen weiter, zählen aber nicht im Papier-Konto) im Mittel gewinnen und besser sind als die erlaubten.
3. **Nachbesprechung mit der KI** (höchstens ein Aufruf, gleiches Modell): Statistik der letzten Runden (Treffer, Ergebnis nach Kosten, typische Fehler: Stop zu eng, Ziel zu weit, Richtung falsch,
   Bewegung kleiner als die Kosten, Ziel näher als die Kosten, gegen den Trend, Hebel) plus die letzten Einzelfälle mit Begründung und Ergebnis. Die KI schreibt höchstens 8 kurze, allgemeine **Lektionen** mit Begründung
   und Beleg-Anzahl und ersetzt veraltete. Sie werden versioniert in der Datenbank gespeichert und stehen in **jedem folgenden Vorhersage-Prompt** (zusammen mit den aktiven festen Regeln).
   Eine Lektion braucht mindestens 2 Belege (`--min-belege-lektion`, vorher 3; weniger wird verworfen). Mit weniger als 4 Belegen (`--belege-gesichert`) gilt sie als **vorläufig** (so im Prompt, im Protokoll und im Bericht
   gekennzeichnet). Bei der nächsten Nachbesprechung bekommt die KI die vorläufigen Lektionen zur Prüfung: bestätigt (mehr Belege) oder gestrichen; kommt eine vorläufige Lektion unverändert mit gleicher Belegzahl zurück,
   streicht das Programm sie selbst („vorläufig, nicht bestätigt“ in `veraltet`).
4. **Messen, ob es hilft:** Jede Vorhersage trägt die Lektionen-Version, die beim Fragen galt. `auswerten` zeigt Trefferquote und Ergebnis je Version (mit n und Bereich), „nachher gegen vorher“ und den Vergleich mit den
   **Kontrollrunden** (`--kontrollrunde 4`: jede 4. Runde ohne Lektionen und ohne Regeltext im Prompt; 0 = keine). Lektionen entstehen aus früheren Fällen und werden an späteren gemessen.

**Feste Sperren ab dem ersten Trade** (keine Lernregeln, unabhängig von `--regeln`; gesperrte Trades werden nicht gehandelt, sondern als **Schatten-Trade** mit denselben Kursen weitergerechnet; die Vorhersage zählt weiter als
Richtung; die Sperre steht in `trades.gesperrt`, die Begründung in `trades.notiz` und im Protokoll). Anlass: Im ersten 30-Minuten-Lauf (Gebühr 0,40 % je Seite) setzte die KI Ziele von 0,25 bis 0,6 %, kleiner als die Kosten
von 0,80 % plus Spread, und handelte dreimal ETH-Short gegen eine Aufwärtsbewegung (alle ausgestoppt, −1,2 bis −1,6 %).
- **Kostensperre** (`--kosten-faktor`, Standard 2,0; 0 = aus; Kürzel `ks`): Ein Trade wird nur eröffnet, wenn |Ziel − Einstieg| / Einstieg ≥ Faktor × (2 × Gebühr + aktueller Spread in %). Bei 0,40 % Gebühr und 0,02 % Spread
  sind das mindestens 1,64 %. Der Vorhersage-Prompt nennt die Kosten in Prozent, den Spread und diese Mindest-Zielweite ausdrücklich; die Obergrenze für den Ziel/Stop-Abstand liegt mindestens 25 % darüber (höchstens 3 % bei
  15 und 5 % bei 60 Minuten), sonst ist bei diesem Horizont kein Trade möglich (steht dann im Prompt).
- **Trendbremse** (`--trend-serie`, Standard 3; 0 = aus; `--trend-sperre-min` 30; `--trend-klar-pct` 0,15; Kürzel `tb`): Lagen die letzten 3 ausgewerteten steigt/faellt-Vorhersagen eines Symbols alle mit derselben Richtung
  falsch, und der Kurs lief jeweils klar (mindestens 0,15 % und über der Schwelle) in die Gegenrichtung, sind Trades in diese Richtung für das Symbol 30 Minuten ab der Auswertung der letzten gesperrt. Danach ist die Richtung von
  selbst wieder frei, jeder weitere klare Fehlschlag verlängert die Sperre, ein Treffer, eine nicht klare Bewegung oder eine andere Richtung beendet die Serie. Jede Vorhersage zählt einzeln (15 und 60 Minuten);
  das Protokoll meldet „TRENDBREMSE“, der Prompt nennt die aktive Sperre.
- **Bericht:** `auswerten` zeigt den Abschnitt „Gesperrte Trades (nur beobachtet, Schatten-Rechnung)“ je Sperrgrund: Anzahl, Ziel/Stop/Ablauf, Ø vor und nach Gebühren und die Summe in EUR, also was die gesperrten Trades
  gebracht hätten (negativ = die Sperre hat den Verlust erspart). Die gesperrten Trades zählen nicht in den Lernregeln (`min_p`, `k`, Hebel, Pause).

**Probelauf (26.10.2026, Cloud, echte Kraken-Kurse und echte KI).** `laufen --dauer 12 --takt 5 --lern-abstand 6 --max-ki-aufrufe 8 --nachlauf 20`: 3 Runden, 12 Vorhersagen, 4 Papier-Trades (alle ETH/EUR Long;
BTC/EUR sagte sechsmal „seitwaerts“ mit p = 0,34 bis 0,36), 6 KI-Aufrufe für Vorhersagen und 1 Nachbesprechung. Ein Vorhersage-Aufruf: Prompt etwa 17.800 Zeichen (rund 12.700 Tokens), Antwort etwa 2.200 Zeichen (rund 1.700 Tokens),
16 bis 27 Sekunden, laut CLI etwa 0,06 USD Listenpreis; die Nachbesprechung: rund 6.900 Zeichen, 13 Sekunden. Nach dem Nachlauf waren 6 Vorhersagen (alle 15 Minuten) ausgewertet, die 4 Trades alle im Minus nach Gebühren
(2 × Ablauf, 2 × Stop; zusammen −45,52 EUR bei 1.000 EUR Einsatz je Trade), die Technikprüfung ohne Befund, die Nachbesprechung lieferte eine Lektion („Trade nur, wenn die erwartete Bewegung klar über den Kosten liegt, sonst seitwaerts“,
Belege 6). Das sind 6 Fälle: **keine Aussage über die KI**, nur der Beleg, dass die Kette (echte Kurse → KI → Papier-Trades → Prüfung → Auswertung → Nachbesprechung) läuft und von Hand nachgerechnet stimmt (z. B. ETH-Long
Ask 2.066,65 → Bid 2.068,48: +0,0886 % vor Gebühren, −0,712 % nach 2 × 0,40 %).

**Ehrliche Grenzen.**
- **Kleine Fallzahlen.** Unter etwa 100 unabhängigen Fällen je Horizont ist fast jede Trefferquote mit Zufall vereinbar. Die Vorhersagen überlappen sich (alle 5 Minuten eine neue für die nächsten 15 bzw. 60 Minuten), die
  echte Zahl unabhängiger Fälle ist deutlich kleiner als n, die Bereiche sind eher zu eng. Ein Fehleralarm bei kleinem n ist ein Hinweis, kein Beweis; kein Alarm heißt nicht „die KI kann es“.
- **Überanpassung.** Lektionen und feste Regeln stammen aus wenigen Dutzend Fällen. Muster in so kleinen Mengen sind oft Zufall. Zählen darf nur die Messung auf später folgenden Fällen und der Vergleich mit den Kontrollrunden,
  und auch die braucht viele Fälle je Gruppe.
- **Eine Marktphase.** Wenige Stunden sind eine Wetterlage (Wochenende, Seitwärtsphase). Ergebnisse gelten nicht automatisch für andere Phasen.
- **Kosten.** Bei 0,40 % je Seite (0,80 % für Hin und Zurück) plus Spread braucht ein Trade auf 15 bis 60 Minuten eine Bewegung von deutlich mehr als 0,8 %. Das ist auf diesen Horizonten selten; entsprechend ist „seitwaerts“
  oft die richtige Antwort. Margin-Eröffnungs- und Übernachtgebühren für den Hebel, Steuern und Schlupf bei schnellen Märkten sind nicht gerechnet. Short und Hebel sind Spielgeld.
- **Prüfung im Minutentakt.** Zwischenspitzen unter einer Minute werden nicht gesehen. Der Rechner muss laufen; bei Standby entstehen Lücken (die betroffenen Fälle werden ausgeschlossen).
- **Die KI ist keine Messgröße ohne Rauschen.** Dieselbe Frage kann anders beantwortet werden; die Prompt-Prüfsumme und der Kontext (komprimiert) jeder Anfrage stehen in der Datenbank (Tabelle `aufrufe`).

## Nachrichten-Radar

`python3 -m kitrader nachrichten abrufen` holt Meldungen aus offenen RSS/Atom-Feeds (45 Feeds von 38 Häusern, Pausen dazwischen) und aus der
freien **GDELT-DOC-API** (weltweit, 65 Sprachen, Suche mit englischen Stichwörtern; Artikel je Region und Ton `timelinetone` je Region/Thema,
höchstens eine Anfrage alle 6 Sekunden). `nachrichten radar` zeigt die Zusammenfassung, `nachrichten suche "oil iran"` durchsucht die
gespeicherten Meldungen. Regeln: nur offizielle Feeds und freie APIs, keine Seiten ausschaben, keine Sperren umgehen, keine in der EU
sanktionierten Medien (Treffer aus GDELT der eingebauten Liste `GESPERRTE_DOMAINS` fallen weg, mit `[nachrichten] gesperrte_domains` ergänzbar; die Liste ist ein Anfang, keine Rechtsauskunft).
Gespeichert wird je Meldung nur Titel, Kurztext aus dem Feed (höchstens 400 Zeichen), Link, Quelle, Zeit (UTC) und Sprache in `nachrichten.db`
(Datenordner), 14 Tage lang. Kein Volltext.

- **Dubletten:** gleiche Seite (Adresse ohne `www`, Tracking-Parameter, Fragment) oder fast gleicher Titel desselben Hauses innerhalb von 72 Stunden zählt einmal.
  Dieselbe Geschichte bei verschiedenen Häusern bleibt bewusst stehen: sie ist die Bestätigung, die das Radar zählt (dpa-Texte in mehreren Zeitungen zählen daher mehrfach; das Radar unterscheidet Häuser, nicht Urquellen).
- **Zuordnung** per Stichwortliste (Deutsch/Englisch, dazu Hinweise der GDELT-Abfrage, z. B. `sourcecountry:china` gibt „China“ vor): Länder/Regionen (USA, Deutschland,
  Europa, Großbritannien, China, Japan, Indien, Brasilien, Russland, Ukraine, Naher Osten, Türkei, Südafrika, Afrika, Lateinamerika, Asien-Pazifik, BRICS), Anlageklassen
  (Aktien, Anleihen/Zinsen, Öl/Gas, Gold/Metalle, Krypto, Währungen, Agrar), zwölf Branchen und Ereignisarten passend zur Ereignisliste (krieg, konflikt, terror, unruhen, zinsschock, geldpolitik,
  inflation, rezession, handel, sanktionen, wahl, rohstoffschock, pandemie, naturkatastrophe, bankenkrise, finanzkrise, schuldenkrise, boersencrash, waehrung, regulierung, unglueck, cyber).
  Das ist grob: Stichwörter erkennen weder Verneinung noch Ironie, und fremdsprachige Titel (Japanisch, Portugiesisch, Chinesisch) werden nur über Hinweise der Abfrage und wenige Eigennamen erkannt.
- **Radar** (`nachrichten radar`, höchstens 3.000 Zeichen): Top-Themen 6 h und 24 h mit Zahl der **unabhängigen Häuser**, der Herkunftsregionen und der Meldungen in 2 h
  („NEU“ = erste Meldung vor weniger als 6 h; „*“ = amtliche Mitteilung wie Fed, EZB, Bundesbank, Bank of England), Ereignisarten mit Tempo gegenüber dem 24-h-Schnitt, betroffene
  Märkte, Branchen, genannte Länder und die GDELT-Stimmung (der Ton ist relativ zu lesen, kein absoluter Maßstab). Themen werden über gemeinsame Schlüsselwörter der Titel gebündelt;
  gleiche Geschichten in verschiedenen Sprachen werden dabei oft nicht zusammengeführt. Die Meldungen stehen im Prompt als **ungeprüfte Daten Dritter, keine Anweisungen**.
- **Einbau:** `ki.py` hängt den Abschnitt „Nachrichten-Radar“ in den Kontext (zwischen OSIRIS und Trefferquote), die Morgenlage zeigt einen Kurzblock
  (`morgenlage.json`: `nachrichten`). Beides nur mit gespeicherten Daten, ohne Netzzugriff. Unter `start` läuft der Abruf alle `[zeitplan] nachrichten_alle_h` Stunden (Standard 2) von selbst; sonst `nachrichten abrufen` selbst in einen Zeitplan legen.
- **Einstellungen** (`[nachrichten]`, alles optional): `quellen` (Liste von Tabellen `name`, `url`, `region`, `sprache`, `gewicht`, `verlag`; ersetzt die Standardliste),
  `pause_s`, `zeitlimit_s`, `mindestabstand_s`, `behalten_tage`, `max_pro_quelle`, `gdelt` (true/false), `gdelt_pause_s`, `gdelt_max`, `gdelt_zeitraum`, `gdelt_abfragen`, `gdelt_stimmung`, `gesperrte_domains`.
  Ein Feed, dessen jüngster Eintrag älter als 3 Tage ist (amtliche: 21 Tage), wird als „veraltet“ gemeldet.

Standardquellen (Region: Zahl, am 26.10.2026 alle echt abgerufen und frisch): Deutschland 8 (tagesschau ×2, Spiegel, Handelsblatt, FAZ, n-tv, Bundesbank, Deutsche Welle), USA 6 (CNBC ×2, MarketWatch,
WSJ ×2, Federal Reserve), Großbritannien 5 (BBC ×2, Guardian, Financial Times, Bank of England), Europa 4 (EZB, Euronews, Le Monde, El País), China 4 (CGTN ×2, SCMP ×2), Indien 3 (Economic Times ×2, The Hindu),
Brasilien 2 (G1, Folha), Lateinamerika 1 (MercoPress), Naher Osten 1 (Al Jazeera), Afrika 2 (allAfrica, Africanews), Japan 3 (Japan Times, Japan Today, Nikkei Asia), Asien-Pazifik 3 (Straits Times, Yonhap, ABC Australia),
Fachdienste 3 (OilPrice, CoinDesk, Cointelegraph). **Geprüft und nicht aufgenommen:** Xinhua (Feed antwortet, enthält aber nur Einträge von 2017/2018), NHK (jüngster Eintrag August 2026), China Daily (RSS 404), Arab News (403),
Gulf News (404), IWF (403), BIZ (404), Kitco (404), Yahoo Finance (404), Korea Herald (kein gültiges XML), Kyodo (404), RT (in der EU sanktioniert, Verbindung abgebrochen), TASS (lieferte Daten; Sanktionsstatus nicht geprüft, daher draußen –
wer sie will, trägt sie selbst unter `[nachrichten] quellen` ein). Russland, Iran und die Golfstaaten sind damit nur über GDELT und Al Jazeera abgedeckt.

## Welt-Rotation

`python3 -m kitrader welt` (Optionen `--ohne-abruf`, `--erzwingen`, `--json`) zeigt, **wer gewinnt und wer verliert**: Rangliste der Regionen (Leitindex je Region: USA S&P 500, Europa Euro Stoxx 50, Deutschland DAX,
Großbritannien FTSE 100, China Hang Seng, Japan Nikkei 225; Indien, Brasilien und Schwellenländer, sobald ein Index vorliegt) und der Anlageklassen (Aktien, Öl/Gas, Gold/Metalle, Agrar, Krypto, Währungen gegen USD,
Rüstung/Tech-Einzelwerte) über **1 Tag, 1 Woche und 1 Monat**, jeweils absolut und **relativ zum Schnitt der Leitindizes** (Prozentpunkte), dazu Zins (US 10 J. in Basispunkten), Dollar-Index und VIX als Anzeige und die stärksten/schwächsten
Einzelwerte. Der Abschnitt „Umschichtung“ nennt, wo der Abstand zwischen stärkster und schwächster Region bzw. Klasse groß ist (Schwellen 1 / 2 / 4 Pp für Tag / Woche / Monat; „von … nach …“); das ist **reine Beobachtung der Kurszahlen**,
kein Nachweis von Geldflüssen und keine Ursache. Fehlende Märkte werden genannt.

- **Quellen:** OSIRIS `/api/markets` (ein Abruf: Indizes, Öl/Gas, Metalle, Agrar, Krypto, Devisen; mit Vortagsschluss und rund einem Monat Tagesschlüssen), Kraken (Tageskerzen BTC/EUR, ETH/EUR; ersetzen dann die OSIRIS-Zeilen
  Bitcoin/Ethereum), der Historie-Speicher (Tagesreihen als Reserve für fehlende Märkte und als Zusatz: Dow Jones; eigene Reihen über `[welt] reihen`, etwa ein indischer oder brasilianischer Index, den man mit
  `python3 -m kitrader.historie einlesen` in den Speicher legt und dann als `leitindizes = { Indien = "Nifty 50" }` benennt). FRED-Reihen (VIX, WTI, EUR/USD, US-Zins) dienen als Ersatz, wenn der Feed den Markt nicht hat und die Reihe geladen ist (auf dem Mac).
- **Schnappschüsse:** Jeder Abruf wird in `welt.db` gespeichert. 1 Woche und 1 Monat rechnen bevorzugt aus den eigenen Schnappschüssen (gleicher Zeitpunkt für alle Märkte, sobald einer 7 ± 1,5 bzw. 30 ± 5 Tage alt ist),
  bis dahin aus der Kursreihe des Feeds (5 Handelstage, bei Krypto 7 Tage; Monat = rund 20 Handelstage). Die Tagesänderung kommt aus dem Vortagsschluss des Feeds. Für eine eigene Historie `welt` regelmäßig laufen lassen.
- **Grenzen:** Leitindizes in Landeswährung (Währungseffekte fehlen); China = Hang Seng (Hongkong), kein Festlandsindex; S&P 500 und Nasdaq 100 sind Futures-Kurse, die außerhalb der Börsenzeit weiterlaufen; bei geschlossenen
  Märkten (Spalte „Markt“ = zu) ist „1 Tag“ der letzte Handelstag; ältere Kurse (Standard 4 Tage) zählen nicht in die Schnitte. Die Indizes in der Quelle sind eine kleine Auswahl, kein Weltportfolio.
- **Einbau:** `ki.py` hängt den Abschnitt „Welt-Rotation“ in den Kontext, die Morgenlage zeigt einen Kurzblock (`morgenlage.json`: `welt`); beides nur mit gespeicherten Daten.
- **Einstellungen** (`[welt]`, alles optional): `kraken` (Paare, `[]` = aus), `mindestabstand_s`, `behalten_tage`, `zeitlimit_s`, `historie`, `reihen`, `leitindizes`, `veraltet_tage`.

## Bedienoberfläche im Browser (die kleine, optional)

Die Hauptoberfläche ist das Markt-Dashboard (`python3 -m kitrader start`, Reiter Trends, KI, Weltlage, Autopilot). Diese kleine Seite bleibt als Alternative:

```
python3 -m kitrader oberflaeche          # öffnet http://127.0.0.1:8850/ im Browser; beenden mit Strg+C
```

Eine Seite mit sechs Reitern. Oben stehen **immer** die Betriebsart (groß und farbig: **PAPIER** blau, **DEMO** gelb,
**ECHTGELD** rot und blinkend) und der rote **NOT-AUS**-Knopf.

| Reiter | Zeigt |
|---|---|
| Übersicht | oben die **Morgenlage** aus `morgenlage.json` (falls vorhanden: Kurs, Veränderung, KI kurz/mittel/lang je Wert, OSIRIS-Veränderung, KI-Trefferquote), dann Konto, Ergebnis heute, offene Positionen und Orders, Stand der Risikogrenzen (Tagesverlust, Positionen, Orders heute, größte Position, jeweils mit Auslastung), ob der Not-Aus aktiv ist, die letzten Ereignisse des Autopiloten. Aktualisiert sich alle 10 Sekunden. |
| Live | je Symbol der Watchlist (`[watchlist] werte`) und des Autopiloten (`autopilot.symbole`) ein **Kerzenchart** (eigenes SVG, keine Fremdbibliothek) der letzten Kerzen aus der Kursquelle (Kerzenlänge wählbar: 1m, 5m, 15m, 1h, 1d; Standard `oberflaeche.live_takt`, Anzahl `oberflaeche.live_kerzen`), mit aktuellem Kurs, der offenen **Position** und den eigenen **Trades**: Einstieg als Dreieck (▲ Long türkis, ▼ Short magenta, damit sie sich nicht mit grünen und roten Kerzen verwechseln), Ausstieg als Kreis mit Ergebnis, **Stop** (rot) und **Ziel** (grün) als gestrichelte Linien; darunter die **letzte KI-Einschätzung** mit Richtung und Wahrscheinlichkeit je Horizont (1 Tag, 3 Monate, 1 Jahr) und ihrem Alter. Die letzte Kerze ist noch nicht abgeschlossen (gestrichelt). Aktualisiert sich alle 30 Sekunden; Zeiten in der Ortszeit des Browsers. Trades kommen im Papierbetrieb aus dem Papierkonto (nur dort stehen auch Ausstiege durch Stop oder Ziel), sonst aus dem Journal (ausgeführte Orders, FIFO gepaart; ein Ausstieg, den der Broker allein ausführt, steht dort nicht). Hat ein Symbol mehrere Positionen zugleich, kann die FIFO-Reihenfolge die Stop-/Ziel-Zuordnung eines abgeschlossenen Trades verschieben. |
| Journal | letzte Entscheidungen mit den **Gründen** der Risikoprüfung; Trefferquote der Einschätzungen je Horizont **mit n**; die jüngsten Einschätzungen |
| Weltlage | die OSIRIS-Lage als Text; „neu abrufen“ holt die Feeds (dauert etwa eine halbe Minute, nicht öfter als alle 5 Minuten) |
| Historie | eine Reihe wählen (z. B. Dow Jones, logarithmisch), Zeitraum und Ereignisart einschränken; Ereignisse sind kleine Striche unter dem Chart – ein Klick darauf (oder in der Liste) zeigt die **Ereignisstudie** (Entwicklung nach 1/3/6/12 Monaten, Tief, Erholung, Quelle) |
| KI | einen Wert eingeben (z. B. `AAPL`) und „Einschätzung anstoßen“; Treiber wie in den Einstellungen, oder „aus“ (zeigt nur den Kontext). Das kann einige Minuten dauern. |

**Not-Aus-Knopf:** Er fragt nach, legt dann die Datei `NOTAUS` an (das sperrt sofort jede Order) und ruft
`alles_schliessen(symbole=…)` des eingestellten Brokers auf (im Papierbetrieb den Papier-Broker), und zwar für die eigenen
Symbole; Fremdes wird gemeldet und bleibt unberührt. Die einzelnen Schritte
(storniert, glattgestellt, Fehler) werden angezeigt und, falls Telegram eingerichtet ist, aufs Handy gemeldet.
**Aufheben geht bewusst nur am Rechner:** `python3 -m kitrader notaus --aufheben`.

**Orders sendet die Oberfläche nie.** Orders macht ausschließlich der Autopilot, und jede geht durch die Risikoprüfung.

**Schutz vor fremden Webseiten und Programmen** (wichtig, weil ein Browser auch Seiten aus dem Internet öffnet):

- Der Server bindet **nur an 127.0.0.1** (dieser Rechner); das lässt sich nicht umstellen. Vom Handy oder von
  anderen Rechnern aus geht es nicht – dafür gibt es Telegram.
- Beim Start wird ein zufälliger **Schlüssel** erzeugt, in die Seite eingebettet und bei jeder Anfrage mitgeschickt;
  ohne ihn (auch beim Lesen der Daten) antwortet der Server mit „403 verboten“. Nach einem Neustart der Oberfläche die Seite neu laden.
- `Host` und `Origin` werden streng geprüft (nur `127.0.0.1:<Port>` und `localhost:<Port>`); eine fremde Seite kann also
  weder Daten lesen noch den Not-Aus auslösen. Es gibt keine CORS-Freigaben.
- Texte von außen (OSIRIS-Meldungen, KI-Antworten) werden nie als HTML eingefügt, und die Seite läuft mit strenger
  Content-Security-Policy.

Der Port steht in `[oberflaeche] port` (Standard 8850) oder per `--port`. Wer auf demselben Rechner Programme laufen
lässt, kann die Seite (und damit den Schlüssel) ebenfalls abrufen – die Oberfläche schützt vor dem Browser, nicht
vor Schadsoftware auf dem Rechner.

## Telegram

Telegram schickt dir Meldungen aufs Handy und lässt dich den Not-Aus auch unterwegs auslösen.

**Einrichten (einmalig, etwa 5 Minuten):**

1. In Telegram den Chat **@BotFather** öffnen (der offizielle Bot mit blauem Haken).
2. `/newbot` schreiben. Er fragt nach einem Namen (beliebig, z. B. „Mein kitrader“) und nach einem Benutzernamen,
   der auf `bot` enden muss (z. B. `dana_kitrader_bot`).
3. BotFather antwortet mit einem **Token** (sieht aus wie `123456789:ABCdef…`). Das ist wie ein Passwort: nicht
   weitergeben, nicht fotografieren, nicht in Dateien des Projekts schreiben.
4. Auf dem Rechner: `python3 -m kitrader telegram einrichten`. Das Programm fragt nach dem Token (die Eingabe wird
   nicht angezeigt). Danach in Telegram **deinen neuen Bot suchen, auf START tippen und „hallo“ schreiben**.
   kitrader zeigt, wer geschrieben hat, und fragt „Bist du das?“. Mit `j` wird die Chat-ID gespeichert, und eine
   Testnachricht kommt an.
5. Gespeichert wird in `~/.config/kitrader/telegram.env` (Rechte 600, nur du). Der Token wird nirgends
   ausgegeben. Prüfen: `python3 -m kitrader telegram test`.

Wichtig: **Nur** der Chat, dessen ID gespeichert ist, zählt. Schreibt jemand anderes deinem Bot (den Namen kann man
finden), wird das ignoriert – ohne Antwort.

**Was gemeldet wird** (sobald der Autopilot mit `python3 -m kitrader autopilot` läuft und Telegram eingerichtet ist;
Testläufe mit `--wiedergabe` melden nichts):

- Start und Stopp des Autopiloten mit Betriebsart (bei Echtgeld ausdrücklich „ECHTES GELD“), Stoppgrund, letzter Stand
- jede Ausführung (Seite, Menge, Symbol, Preis, Stop, Ziel, Begründung) und das Ergebnis abgeschlossener Trades
- Ablehnungen der Risikoprüfung, **zusammengefasst** (alle `telegram.ablehnungen_alle_s` = 10 Minuten eine Nachricht, mit Zahl und Gründen)
- Tagesverlustgrenze erreicht (die eigenen Positionen werden glattgestellt), erreichtes Gewinnziel, Position ohne Schutz-Stop,
  fremde Position im Konto, Not-Aus (aus Oberfläche, Kommandozeile oder Telegram) und Aufheben
- Tagesabschluss mit dem Ergebnis

Gesendet wird im Hintergrund; ist Telegram nicht erreichbar, steht nur eine Warnung im Protokoll – der Handel läuft
unbeeinflusst weiter.

**Not-Aus per Telegram: die Wache.** Die Wache fragt Telegram laufend ab (Long-Polling). Sie läuft

- im Autopiloten mit, wenn in den Einstellungen `[telegram] wache = true` steht, oder
- für sich: `python3 -m kitrader telegram wache` (z. B. in einem zweiten Terminal; bei `demo`/`echt` mit `--modus`).

Nur zwei Wörter aus deinem Chat wirken (Groß/Klein egal, auch „Stopp“, „/stop“):

- **`stop`**: legt `NOTAUS` an, storniert alles, stellt alles glatt und schickt dir die einzelnen Schritte zurück.
  Aufheben: `python3 -m kitrader notaus --aufheben` am Rechner.
- **`status`**: kurze Übersicht (Betriebsart, Gesamtwert, Ergebnis heute, Positionen, Orders, Not-Aus).

Alles andere – und alles aus anderen Chats – wird ignoriert. Nachrichten, die älter als `telegram.max_alter_s`
(10 Minuten) sind, führt die Wache nicht mehr aus (damit ein vergessenes „stop“ von gestern nicht heute zuschlägt);
sie antwortet dann, dass sie es nicht getan hat. Nur **eine** Wache pro Bot: läuft schon eine, meldet Telegram einen Konflikt.

**Grenzen, die man kennen muss:**

- Der Not-Aus per Telegram geht nur, solange die Wache **läuft**, der Rechner **online** ist und Telegram erreichbar ist.
  Er ist eine Zusatzsicherung, kein Ersatz für die Schutz-Stops beim Broker und den Not-Aus am Rechner.
- Telegram-Bots sind nicht Ende-zu-Ende-verschlüsselt: Symbole, Mengen und Ergebnisse laufen über Telegram-Server.
- Wer Zugriff auf dein Telegram-Konto hat, kann „stop“ schicken – das sperrt nur den Handel, es kann damit niemand kaufen.
- Die Meldungen des Autopiloten kommen über den Beobachter-Haken (`Autopilot.beobachter`, siehe unten). Stop-/Ziel-Ausführungen
  beim Broker (außerhalb eines Durchlaufs) meldet der Autopilot selbst nur im Papierbetrieb, und zwar als „Ergebnis: Trade …“.

## Beobachter-Haken im Autopiloten

`Autopilot.beobachter` ist eine Liste von Funktionen `f(art: str, daten: dict) -> None`. Der Autopilot ruft sie bei diesen
Ereignissen (`autopilot.EREIGNISSE`; `daten` enthält immer `modus`):

| Ereignis | Daten |
|---|---|
| `start` | `strategie`, `symbole` |
| `ende` | `grund` (`gestoppt`, `abbruch`, `fehler_folge`, `tastatur`, `fehler`), `text`, `stand` |
| `entscheidung` | `id`, `symbol`, `richtung`, `ok` (None = nicht geprüft), `gruende`, `notiz` |
| `ablehnung` | `symbol`, `seite`, `gruende` |
| `ausfuehrung` | `symbol`, `stand` (OrderStand), `entscheidung_id` |
| `ergebnis` | `symbol`, `text`, `betrag` (abgeschlossener Trade, netto) |
| `tagesverlust` | `text`, `schliessen` |
| `notaus` | `text`, `schritte`, `aufgehoben`, `vollstaendig` |
| `tagesabschluss` | `text` |
| `ziel_erreicht` | `symbol`, `ziel`, `preis`, `text` |
| `warnung` | `art` (`ohne_schutz`, `fremde_position`), `text` |

Ein Fehler in einem Beobachter wird nur protokolliert (Logger `kitrader.autopilot`) und erreicht den Handel nie; die übrigen
Beobachter bekommen das Ereignis trotzdem. Beobachter sollen schnell sein (Telegram stellt in eine Warteschlange). Das Journal
wird nicht mehr umhüllt; `telegram.py` nutzt den Haken.

## Eigene Strategie

Eine Klasse von `kitrader.strategie.Strategie` mit `bewerten(kerzen, lage) -> Signal | None` ableiten, in
`strategie/__init__.py` unter `STRATEGIEN` eintragen. Eine Strategie sendet nie selbst Orders: Menge (aus dem
Risiko je Trade) und Order baut `signal_zu_order`, und die Order geht durch `risiko.pruefen`. `gitter` gibt die
Parameterwerte an, die der Backtest auf dem Trainingsteil durchprobiert.

## Tests

```
cd /home/user/finanz-portal && PYTHONPATH=src python3 -m unittest discover -s tests/kitrader -t .
```

Alle Tests laufen offline mit erfundenen Daten (für Telegram mit einem nachgebildeten Telegram-Server, für die
Oberfläche mit einem Server im Hintergrund auf einem freien lokalen Port).

## Offene Punkte

- Die Anbindungen an IBKR und Kraken (`broker/ibkr.py`, `broker/kraken.py`) sind ein eigener Baustein und wurden
  mit diesem Kern **nicht an einem echten Konto erprobt**. Vor jedem Einsatz im Demo-Betrieb prüfen, dass Konto,
  Positionen, Orders und Stops wirklich so ankommen wie erwartet.
- Die Strategien nutzen nur Kerzen. Sie kennen keine Nachrichten, Ereignisse (Quartalszahlen) oder Marktbreite;
  Weltlage und KI-Einschätzung (`osiris.py`, `ki.py`) wirken nur über den optionalen KI-Filter oder die Strategie `ki`.
- Der Treiber `claude_cli` ist gegen die echte CLI geprüft (volle Einschätzung für BTC/EUR am 26.10.2026, 23 s),
  `anthropic` nur gegen einen nachgebildeten Server.
- Der Tageswechsel im Autopiloten richtet sich nach UTC.
- Die Bedienoberfläche ist im echten Chromium geprüft (Cloud, 26.10.2026), noch **nicht** in deinem eigenen Browser; Telegram
  nur gegen einen nachgebildeten Server, **nicht** gegen den echten Telegram-Dienst. Beim ersten Einsatz: `telegram test`,
  dann bewusst im Papierbetrieb einen Not-Aus per „stop“ ausprobieren.
- **IBKR-Schutz-Stops laufen nur einen Tag** (`DAY`, Anbindung `broker/ibkr.py`). Im Demokonto prüfen, dann auf `GTC` stellen;
  bis dahin gilt: Position über Nacht nur mit Bedacht (der Autopilot stellt im Daytrading ohnehin vor Handelsschluss glatt).
- **`konto/positionen/orders/notaus --modus echt`** bauen einen Echtgeld-Broker ohne Freigabedatei. Das ist so gelassen: Lesen
  und Not-Aus müssen immer gehen. Orders sendet nur der Autopilot, und der verlangt die Freigabe.
- Neu und **nur gegen nachgebildete Server geprüft**: IBKR-Vertragsdaten (Währung) und Devisenpaare (`broker/BROKER.md`), Kraken
  `Trades` für die Kurszeit (Format gelesen, nicht mit einem Konto), Kraken-Hebel nur für Short/Schließen (ob Kraken zum Schließen
  einer Margin-Position `leverage` verlangt, ist unbestätigt). Der Papierhandel rechnet Kurse in fremder Währung weiterhin
  1:1 in die Kontowährung (nur die Risikoprüfung rechnet um).
- Die Seite der Oberfläche (`oberflaeche/seite.html`) muss bei einer Installation mit `pip install .` als Paketdatei
  mitgenommen werden (`pyproject.toml`: `package-data` um `*.html` ergänzen); mit `pip install -e .` und im Repo funktioniert es.
