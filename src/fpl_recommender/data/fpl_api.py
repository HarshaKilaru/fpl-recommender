import time
import requests
from typing import Dict, Any, List

FPL_BASE = "https://fantasy.premierleague.com/api"

def fetch_bootstrap_static(session: requests.Session = None) -> Dict[str, Any]:
    """
    Core FPL dataset: players, teams, element_types, events (GWs), etc.
    """
    s = session or requests.Session()
    r = s.get(f"{FPL_BASE}/bootstrap-static/")
    r.raise_for_status()
    return r.json()

def fetch_fixtures(session: requests.Session = None) -> List[Dict[str, Any]]:
    """
    All fixtures with difficulty ratings and home/away flags.
    """
    s = session or requests.Session()
    r = s.get(f"{FPL_BASE}/fixtures/")
    r.raise_for_status()
    return r.json()

def throttle(seconds: float = 0.5):
    time.sleep(seconds)
