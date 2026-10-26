#!/bin/bash
# kitrader beenden: Doppelklick im Finder. Beendet den laufenden "kitrader-starten" sauber (wie Strg+C in dessen Fenster).

cd "$(dirname "$0")" || exit 1

finde_python() {
  local kandidat pfad
  for kandidat in python3.14 python3.13 python3.12 python3.11 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    pfad="$(command -v "$kandidat" 2>/dev/null)" || continue
    if [ "$pfad" = "/usr/bin/python3" ] && [ "$(uname)" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then continue; fi
    if "$pfad" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      echo "$pfad"
      return 0
    fi
  done
  return 1
}

PY="$(finde_python)" || { echo "Python 3.11 oder neuer fehlt. Bitte im Fenster von kitrader Strg+C drücken."; read -n 1 -s -r -p "Taste drücken ..."; exit 1; }
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
"$PY" -m kitrader start --beenden
status=$?
echo
read -n 1 -s -r -p "Taste drücken zum Schließen ..."
exit "$status"
