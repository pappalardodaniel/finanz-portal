#!/bin/bash
# kitrader starten: Doppelklick im Finder (öffnet das Terminal).
# Startet alles auf einmal im PAPIERBETRIEB (Simulation mit echten Kursen, kein echtes Geld) und öffnet das Markt-Dashboard (Hauptoberfläche) im Browser.
# Beenden: Strg+C in diesem Fenster. Nichts wird installiert; ist Python zu alt oder fehlt es, sagt dieses Fenster es klar.

cd "$(dirname "$0")" || { echo "Der Ordner dieses Programms ist nicht erreichbar."; read -n 1 -s -r -p "Taste drücken zum Schließen ..."; exit 1; }

finde_python() {
  local kandidat pfad
  for kandidat in python3.14 python3.13 python3.12 python3.11 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    pfad="$(command -v "$kandidat" 2>/dev/null)" || continue
    # macOS: /usr/bin/python3 ist ohne Entwicklerwerkzeuge nur ein Platzhalter, der einen Installationsdialog öffnen würde: nicht anfassen
    if [ "$pfad" = "/usr/bin/python3" ] && [ "$(uname)" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then continue; fi
    if "$pfad" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      echo "$pfad"
      return 0
    fi
  done
  return 1
}

PY="$(finde_python)" || {
  echo
  echo "kitrader braucht Python 3.11 oder neuer. Es wurde keins gefunden (oder nur ein älteres)."
  echo "Es wird NICHTS automatisch installiert. Python 3.11+ gibt es unter https://www.python.org/downloads/ ."
  echo "Danach diese Datei noch einmal doppelklicken."
  echo
  read -n 1 -s -r -p "Taste drücken zum Schließen ..."
  exit 1
}

export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

if [ -n "$KITRADER_TROCKEN" ]; then      # nur zum Prüfen dieser Datei: zeigt, was gestartet würde
  echo "Python: $PY ($("$PY" --version 2>&1))"
  echo "Befehl: $PY -m kitrader start --modus papier"
  exit 0
fi

echo "kitrader wird gestartet (Papierbetrieb, kein echtes Geld) ..."
"$PY" -m kitrader start --modus papier
status=$?
echo
if [ "$status" -ne 0 ]; then
  echo "kitrader wurde mit einem Fehler beendet (Code $status). Der Grund steht weiter oben."
  read -n 1 -s -r -p "Taste drücken zum Schließen ..."
else
  echo "kitrader ist beendet. Dieses Fenster kann geschlossen werden."
fi
exit "$status"
