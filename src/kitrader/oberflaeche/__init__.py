"""Bedienoberfläche im Browser (nur auf diesem Rechner erreichbar). Start: python3 -m kitrader oberflaeche"""
from .server import Oberflaeche, bauen, laufen

__all__ = ["Oberflaeche", "bauen", "laufen"]
