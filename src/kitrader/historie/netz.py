"""Eigener kleiner HTTP-Abruf (urllib): ehrlicher User-Agent, Wiederholungen bei 429, 5xx und Verbindungsabbruch.

Es wird nichts umgangen: Wer ablehnt (403, 404, Bot-Schutz), wird sofort als Fehler gemeldet.
"""
from __future__ import annotations

import gzip
import http.client
import os
import ssl
import time
import urllib.error
import urllib.request

USER_AGENT = "historie/0.1 (privates Lernprojekt; urllib)"


def _ssl_kontext() -> ssl.SSLContext:
    """SSL_CERT_FILE, sonst certifi (falls installiert), sonst Systemstandard."""
    kandidaten = [os.environ.get("SSL_CERT_FILE")]
    try:
        import certifi
        kandidaten.append(certifi.where())
    except ImportError:
        pass
    for datei in kandidaten:
        if datei and os.path.exists(datei):
            return ssl.create_default_context(cafile=datei)
    return ssl.create_default_context()


_OEFFNER = None


def _oeffner():
    global _OEFFNER
    if _OEFFNER is None:
        _OEFFNER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_kontext()))
    return _OEFFNER


def holen(url: str, kopf: dict | None = None, versuche: int = 3, timeout: int = 120) -> bytes:
    """Lädt eine URL und gibt den Inhalt zurück (gzip wird entpackt; zip-Dateien wie .xlsx bleiben unberührt)."""
    h = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **(kopf or {})}
    letzter: Exception | None = None
    for i in range(versuche):
        try:
            with _oeffner().open(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                roh = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip" or roh[:2] == b"\x1f\x8b":
                    roh = gzip.decompress(roh)
                return roh
        except urllib.error.HTTPError as e:
            if (e.code < 500 and e.code != 429) or i == versuche - 1:
                raise
            letzter = e
            pause = float(e.headers.get("Retry-After") or 15 * 2 ** i) if e.code == 429 else 2 ** i
            time.sleep(min(pause, 120))
            continue
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, OSError) as e:
            letzter = e                                   # u. a. RemoteDisconnected, Verbindung zurückgesetzt
            if i == versuche - 1:
                raise
        time.sleep(2 ** i)
    raise letzter or RuntimeError("unerreichbar")
