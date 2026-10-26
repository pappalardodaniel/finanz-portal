# Broker-Anbindungen für kitrader: Interactive Brokers und Kraken

Diese Anleitung ist für Nicht-Programmierer. Sie erklärt, wie du die beiden Broker einrichtest und was die
Schutzschalter bedeuten. Zugangsdaten gehören **nie** ins Repository, nie in den Chat, nie in eine Mail.

**Kurzfassung der Schutzregeln**

| Broker | Schutz | Standard |
|---|---|---|
| Interactive Brokers (IBKR) | sendet nur an Demokonten (Kontonummer beginnt mit `DU`) | an; Echtgeld nur mit `echtgeld_erlaubt=True` |
| Kraken | `nur_pruefen`: Kraken prüft Orders, führt sie aber nicht aus | **an**, bis du es ausdrücklich abschaltest |

Beide Broker kennen keine Auszahlungen und brauchen nie eine Auszahlungs-Berechtigung.

---

## 1. Interactive Brokers (Demokonto)

IBKR hat kein einfaches "API-Passwort". Stattdessen läuft auf deinem Rechner ein kleines Programm, das
**Client Portal Gateway**. Du meldest dich dort selbst im Browser an, kitrader spricht danach nur mit diesem
Gateway (`https://localhost:5000`) und kennt dein Passwort nie.

### 1.1 Demokonto anlegen

1. Du brauchst ein normales IBKR-Konto (Eröffnung auf der IBKR-Webseite). Das Demokonto (Paper Trading) hängt daran.
2. Im Client Portal anmelden, oben rechts das Kopf-Symbol, **Einstellungen**, links unter *Kontokonfiguration*
   **Paper-Trading-Konto**.
3. Dort stehen **Benutzername** und **Kontonummer** des Demokontos. Die Kontonummer beginnt mit `DU`.
   Falls du das Passwort nicht kennst: *Paper-Trading-Passwort zurücksetzen* und ein eigenes vergeben.
4. Auf derselben Seite kannst du die **Marktdaten verknüpfen**, damit das Demokonto Kurse bekommt. Ohne Marktdaten
   liefert IBKR keine aktuellen Kurse, und kitrader meldet dann "Kein aktueller Kurs".

Wichtig: Im Gateway gibt es keinen Schalter "Demo/Echt". Du bist im Demokonto, **wenn du dich mit dem
Paper-Benutzernamen anmeldest**.

### 1.2 Gateway herunterladen und starten

1. **Java** installieren, falls nicht vorhanden. Test im Terminal: `java -version`.
2. Das **Client Portal Gateway** von IBKR herunterladen (Download-Link auf der IBKR-Seite
   "Installation & Authentication", siehe Quellen), die ZIP-Datei entpacken (Ordner `clientportal.gw`).
3. Terminal im Ordner `clientportal.gw` öffnen und starten:
   - Windows: `bin\run.bat root\conf.yaml`
   - macOS/Linux: `bin/run.sh root/conf.yaml`
4. Das Fenster offen lassen, solange kitrader laufen soll.

### 1.3 Im Browser anmelden

1. `https://localhost:5000` öffnen. Der Browser warnt vor dem Zertifikat (das Gateway benutzt ein selbst
   erzeugtes). Für diese Adresse ist die Warnung normal: weiter zur Seite.
2. Mit dem **Paper-Benutzernamen** und dem Paper-Passwort anmelden (ggf. Zwei-Faktor-Bestätigung).
3. Wenn "Client login succeeds" erscheint, ist das Gateway bereit.

### 1.4 In kitrader benutzen

```python
from kitrader.broker.ibkr import IbkrBroker
b = IbkrBroker()                 # Standard: https://localhost:5000/v1/api
print(b.pruefen())               # {'konto': 'DU...', 'papier': True, 'waehrung': ...}
print(b.konto())
halte = b.sitzung_halten()       # Hintergrundfaden, "tickelt" jede Minute; halte.set() beendet ihn
```

- Die Sitzung endet, wenn 5 Minuten lang nichts angefragt wird. `sitzung_halten()` verhindert das.
- Ein IBKR-Benutzername darf nur **eine** Sitzung gleichzeitig haben. Meldest du dich z. B. in der Handelsplattform
  oder der App mit demselben Namen an, wirft das Gateway die andere Sitzung raus. kitrader meldet das als
  "konkurrierende Sitzung".
- Läuft das Gateway nicht oder bist du nicht angemeldet (oder die Anmeldung ist abgelaufen), bekommt kitrader
  einen `BrokerFehler` mit genau diesen Schritten. Dann: Gateway starten, im Browser neu anmelden, erneut versuchen.

### 1.5 Was kitrader bei IBKR automatisch tut und was nicht

- **Zertifikat:** Die Prüfung ist nur für `localhost`, `127.0.0.1` und `::1` abgeschaltet. Für jede andere Adresse
  bleibt sie an (im Test belegt).
- **Rückfragen von IBKR** (z. B. "Sind Sie sicher?"): Nur vier harmlose Hinweise werden automatisch bestätigt,
  die vor den Risiken von Markt- und Stop-Orders warnen (`o10151`, `o10152`, `o10153`, `o10331`). Alles andere
  (Preis zu weit vom Markt `o163`, keine Marktdaten `o354`, Größen- und Wertgrenzen `o383`/`o451`,
  Tickgröße `o382`, unbekannte Meldungen) wird **nicht** bestätigt. Du bekommst einen Fehler mit IBKRs Text, und
  die Order wird nicht gesendet.
- **Schutzorders:** Stop-Loss und Take-Profit werden als Bracket-Order zusammen mit der Hauptorder gesendet
  (`cOID`/`parentId`), IBKR verwaltet sie dann selbst.
- **Symbole:** Ein Symbol wie `AAPL` wird über IBKRs Suche in eine `conid` umgewandelt. Ist das Symbol mehrdeutig
  (z. B. mehrere Börsen ohne eindeutige US-Notierung), bricht kitrader mit einer Liste ab. Dann die `conid`
  als `Order.kennung` angeben.
- **Echtgeld gesperrt:** Meldest du dich mit einem echten Konto an (Kontonummer beginnt nicht mit `DU`), liest
  kitrader zwar, **sendet aber keine Orders**, außer du setzt ausdrücklich `IbkrBroker(echtgeld_erlaubt=True)`.
- **Kurszeit:** Die Zeit des Kurses ist das Feld `_updated` des Snapshots. Fehlt es, setzt kitrader die Abrufzeit ein und
  kennzeichnet den Kurs als `zeit_geschaetzt`: die Risikoprüfung lässt damit **keine Eröffnung** zu (Schließen bleibt erlaubt).
- **Währung und Wechselkurs:** Die Währung des Instruments kommt aus den Vertragsdaten (`/iserver/contract/{conid}/info`,
  Feld `currency`), die Wechselkurse aus dem Devisenpaar (Typ `CASH`, z. B. `EUR.USD`, über `/iserver/secdef/search`).
  Beides ist **gegen ein echtes Gateway nicht erprobt**. Liest kitrader die Währung nicht, gilt der Kurs wie bisher als in
  Kontowährung; findet es kein Devisenpaar, greift die Einstellung `wechselkurse`, und ohne beides wird in fremder Währung
  nicht eröffnet. Im Demokonto prüfen: `IbkrBroker(...).kurs("AAPL").waehrung` und `.wechselkurs("EUR", "USD")`.
- **Anfragetempo:** kitrader hält die von IBKR genannten Abstände ein (10 Anfragen pro Sekunde, einige Abfragen
  nur alle 5 Sekunden). Bei Überschreitung sperrt IBKR die IP bis zu 10 Minuten.

---

## 2. Kraken

### 2.1 API-Schlüssel mit minimalen Rechten anlegen

In deinem Kraken-Konto unter Einstellungen den Bereich **API** öffnen und einen neuen Schlüssel erzeugen.
Lege **einen eigenen Schlüssel nur für kitrader** an (Kraken verlangt streng steigende "Nonce"-Zahlen pro Schlüssel;
läuft derselbe Schlüssel in einem zweiten Programm, gibt es Fehler).

Rechte, die du ankreuzt:

- **Funds permissions: Query** (Kontostand abfragen)
- **Orders and trades:** offene und geschlossene Orders abfragen, Orders anlegen und ändern, Orders stornieren

Rechte, die du **nicht** ankreuzt: alles mit **Withdraw** (Auszahlung), **Deposit** (Einzahlung), Datenexport,
Konto-Einstellungen. Kann der Schlüssel nicht auszahlen, kann auch ein Fehler oder Diebstahl des Schlüssels kein
Geld abheben. Gibt es bei Kraken eine Beschränkung auf bestimmte IP-Adressen, nutze sie.

Kraken zeigt dir **API-Schlüssel (Key)** und **Private Key (Secret)**. Das Secret wird nur einmal angezeigt.

### 2.2 Schlüsseldatei anlegen (`chmod 600`)

Im Terminal (macOS/Linux):

```bash
mkdir -p ~/.config/kitrader
nano ~/.config/kitrader/kraken.env       # Inhalt unten einfügen, speichern
chmod 600 ~/.config/kitrader/kraken.env  # nur du darfst die Datei lesen
```

Inhalt der Datei (ohne Anführungszeichen, ohne Leerzeichen um das `=`):

```
KRAKEN_API_KEY=dein-api-key
KRAKEN_API_SECRET=dein-private-key
```

kitrader weigert sich, die Datei zu lesen, wenn sie für andere Benutzer lesbar ist, und sagt dir den `chmod`-Befehl.
(Diese Prüfung gibt es nur unter macOS/Linux. Unter Windows die Datei im eigenen Benutzerordner ablegen.)
Alternativ funktionieren die Umgebungsvariablen `KRAKEN_API_KEY` und `KRAKEN_API_SECRET`; sie haben Vorrang vor der Datei.

Die Datei liegt **außerhalb** des Repositorys. Schlüssel nie in eine Datei im Projektordner schreiben, nie
in `git add` aufnehmen.

### 2.3 Was `nur_pruefen` bedeutet

Kraken Spot hat keinen Demomodus. Deshalb gibt es diesen Schalter:

- **`nur_pruefen` an (Standard):** Jede Order geht mit `validate=true` an Kraken. Kraken prüft sie vollständig
  (Guthaben, Mindestmenge, Preisstufe, Rechte), **führt sie aber nicht aus**. Du siehst eine Antwort wie
  "buy 0.01 XBTEUR @ limit 50000.0". kitrader liefert dafür einen `OrderStand` mit Status `storniert`, einer Kennung
  `GEPRUEFT-...` und der Meldung "NUR GEPRÜFT (validate=true), nicht ausgeführt". Es entsteht nichts Offenes.
- **`nur_pruefen` aus:** Orders werden wirklich ausgeführt, mit echtem Geld.

Abschalten geht nur ausdrücklich: in der Schlüsseldatei `KRAKEN_NUR_PRUEFEN=0` (auch `false`, `nein`, `aus`, `no`,
`off` zählen als Nein), oder im Code `KrakenBroker(nur_pruefen=False)`. Jeder andere Wert, ein Tippfehler oder eine
fehlende Zeile lässt den Schutz an.

Empfohlene Reihenfolge:

1. Nur lesen: `b = KrakenBroker(); print(b.konto()); print(b.kurs("XBTEUR"))`.
2. Mit `nur_pruefen` (an) ein paar Orders prüfen lassen, auch mit Stop-Loss, und die Kraken-Antwort ansehen.
3. Erst dann, mit kleinem Betrag, `nur_pruefen` abschalten und beobachten. Stornieren (`CancelOrder`) ist immer echt,
   auch bei `nur_pruefen`.

### 2.4 Verhalten im Detail

- **Konto:** `konto()` zeigt Guthaben und Gesamtwert in der gewählten Währung (Standard EUR, `KrakenBroker(waehrung="USD")`).
  Kraken Spot ist kein Demokonto, daher ist `papier` immer `False`.
- **Positionen:** Kraken Spot kennt keine "Positionen" für gekaufte Coins. `positionen()` zeigt daher Margin-Positionen
  (`OpenPositions`). Coins als Bestand zählen nur für Paare, die du ausdrücklich nennst:
  `KrakenBroker(spot_paare=("XBTEUR",))`. Das hat Absicht: der Not-Aus (`alles_schliessen`) soll nicht deine übrigen
  Coins verkaufen. Weil Kraken bei Spot keinen Einstandspreis liefert, wird dort der aktuelle Kurs als Einstand
  eingesetzt (Ergebnis 0).
- **Short:** nur über Margin. Standard: `hebel=None`, also kein Short; ein Verkauf ohne Bestand wird von Kraken mit
  "Nicht genug Guthaben" abgelehnt. Mit `KrakenBroker(hebel=2)` bekommt `leverage=2` **nur** eine Order, die einen Short
  eröffnet oder vergrößert (Verkauf ohne Spot-Bestand), und die Order, die eine offene Margin-Position schließt (Gegenseite
  derselben Menge). Der Verkauf von Spot-Bestand bekommt nie Hebel, ein normaler Kauf auch nicht. Dafür fragt kitrader vor
  jeder solchen Order `OpenPositions` und `Balance` ab. Unklare Mischfälle (Verkauf über den Spot-Bestand hinaus, Schließen
  über die Margin-Position hinaus) werden **nicht gesendet**, sondern als Fehler gemeldet. Das ist ungetestet gegen echtes
  Geld; Margin muss bei Kraken für dein Konto freigeschaltet sein.
- **Stop-Loss und Take-Profit:** Kraken erlaubt in einer Order genau **eine** bedingte Schlussorder (`close[...]`).
  - nur Stop-Loss: wird angehängt (`close[ordertype]=stop-loss`)
  - nur Take-Profit: wird angehängt (`take-profit`)
  - beides: der **Stop-Loss** wird angehängt, der Take-Profit **nicht**. Das steht in der Meldung des `OrderStand`.
    Grund: zwei Orders auf denselben Bestand sperren sich bei Spot gegenseitig; bei Margin gäbe es ohne "eine hebt die
    andere auf" eine ungewollte Gegenposition. Das Ziel überwacht dann der **Autopilot selbst**: Der Broker meldet
    `fuehrt_ziel_als_order(order) == False`, der Autopilot merkt sich das Ziel im Journal (übersteht einen Neustart) und
    stellt bei Erreichen glatt (der Stop wird dabei storniert).
- **Gültigkeit:** `gueltig="tag"` heißt bei Kraken "bis 00:00 UTC" (`timeinforce=GTD`), `"gtc"` bis zur Stornierung.
- **Rundung und Mindestmengen:** Menge wird abgerundet, Preise auf die Preisstufe des Paars gerundet. Liegt die Menge
  unter Krakens Minimum, wird gar nichts gesendet.
- **Netzfehler bei einer Order:** Läuft eine Order in eine Zeitüberschreitung, ist unklar, ob sie angekommen ist.
  kitrader wiederholt sie **nie** automatisch, sondern meldet "Ausgang unklar" mit der eigenen Order-Kennung
  (`cl_ord_id`). Dann zuerst die offenen Orders ansehen.
- **Fehlermeldungen:** Die Fehlerliste von Kraken (`error`) wird auf Deutsch erklärt, der Originalcode steht in
  eckigen Klammern dahinter, z. B. `[EOrder:Insufficient funds]`.
- **Tempo:** kitrader bildet Krakens Aufrufzähler nach (Starter: Maximum 15, Abbau 0,33 pro Sekunde;
  `KrakenBroker(stufe="intermediate")` oder `"pro"` für höhere Stufen) und wartet bei Bedarf. Nach "Rate limit exceeded"
  wird 10 Sekunden gewartet.
- **Kurszeit:** Der Kraken-Ticker enthält keinen Zeitstempel. `Kurs.zeit` ist deshalb die Zeit des **letzten Handels**
  (`/0/public/Trades?count=1`, Zeit in Sekunden). Gibt es sie nicht (Abruf scheitert, keine Handelsdaten), ist die
  Abrufzeit nur geschätzt: `Kurs.zeit_geschaetzt = True`, und die Risikoprüfung lässt keine Eröffnung zu (Schließen bleibt
  erlaubt). In einem wenig gehandelten Paar kann der letzte Handel älter sein als `risiko.kurs_max_alter_s` (60 s); dann
  wird nicht gehandelt. Das Antwortformat von `Trades` wurde am 25.10.2026 gegen die öffentliche Schnittstelle gelesen.
- **Währung und Wechselkurs:** `Kurs.waehrung` ist die Quote-Währung des Paars. `wechselkurs("EUR", "USD")` nimmt den
  Kurs des Paars `EURUSD` (Kehrwert, wenn nur `USDEUR` existiert); gibt es kein Paar, ist das Ergebnis `None`.
- **Kerzen:** höchstens die letzten 720; die letzte Kerze ist die laufende, noch nicht abgeschlossene.

---

## 3. Tests

```bash
cd /home/user/finanz-portal
PYTHONPATH=src python3 -m unittest tests.kitrader.test_broker_ibkr tests.kitrader.test_broker_kraken
```

Die Tests laufen offline gegen nachgebildete Server. Sie belegen die Logik (Aufbau der Anfragen, Auswertung der
dokumentierten Antworten, Fehlerfälle, Signatur gegen das offizielle Kraken-Beispiel). Sie belegen **nicht**, dass ein
echtes Gateway oder echtes Kraken genauso antwortet.

**Bisher nicht gegen echte Systeme geprüft:**

- IBKR: Antwortformate des Gateways (insbesondere Bracket-Antwort mit mehreren `order_id`, Orderliste, Kerzen-Volumen
  in Einheiten, die das Gateway liefert), Ordertyp `STOP_LIMIT`, Zeitraum-Angaben bei `marketdata/history`.
- Kraken: AddOrder mit `close[...]`, `timeinforce=GTD`/`expiretm` und `cl_ord_id` gegen die echte Schnittstelle,
  Margin mit `leverage` (inklusive der Frage, ob Kraken zum Schließen einer Margin-Position `leverage` verlangt). Lasse das
  zuerst mit `nur_pruefen` (an) von Kraken prüfen.

---

## Quellen

IBKR (offizielle Web-API-Doku, gelesen):

- Installation und Anmeldung des Gateways: https://www.interactivebrokers.com/docs/web-api/authentication/cpgw/installation-authentication
- Demokonto in der Web API: https://www.interactivebrokers.com/docs/web-api/authentication/paper
- Sitzungen (5-Minuten-Regel, eine Sitzung pro Benutzername): https://www.interactivebrokers.com/docs/web-api/authentication/trading
- Neue Order, Bracket-Orders, Rückfragen, Unterdrückbare Meldungen:
  https://www.interactivebrokers.com/docs/web-api/trading/orders/new-order-example ,
  https://www.interactivebrokers.com/docs/web-api/trading/orders/submitting-bracket-orders ,
  https://www.interactivebrokers.com/docs/web-api/trading/orders/order-reply-messages ,
  https://www.interactivebrokers.com/docs/web-api/trading/orders/suppressible-message-i-ds
- Marktdaten-Schnappschuss, Anfragetempo: https://www.interactivebrokers.com/docs/web-api/trading/market-data/top-of-book-snapshots ,
  https://www.interactivebrokers.com/docs/web-api/trading/usage-and-availability/pacing-limitations
- Referenz (Endpunkte): https://www.interactivebrokers.com/docs/web-api/api-reference/trading/trading-market-data/get-md-history ,
  `.../trading-contracts/get-contract-symbols`, `.../trading-portfolio/get-paginated-positions`,
  `.../trading-portfolio/get-portfolio-summary`, `.../trading-session/get-session-token`
- Die Seiten https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/ und
  https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-ref/ sind die Campus-Fassungen derselben Doku
  (beim Bau nicht abrufbar, die gleichen Inhalte standen unter `/docs/web-api/`).

Kraken (offizielle Doku, gelesen):

- Signatur und Beispiel: https://docs.kraken.com/api/docs/guides/spot-rest-auth
- Fehlercodes: https://docs.kraken.com/api/docs/guides/spot-errors
- Ratenbegrenzung: https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits und
  https://docs.kraken.com/api/docs/guides/spot-ratelimits
- Endpunkte: https://docs.kraken.com/api/docs/rest-api/add-order ,
  `.../cancel-order`, `.../get-ticker-information`, `.../get-ohlc-data`, `.../get-tradable-asset-pairs`,
  `.../get-account-balance`, `.../get-trade-balance`, `.../get-open-orders`, `.../get-closed-orders`,
  `.../get-orders-info`, `.../get-open-positions`
