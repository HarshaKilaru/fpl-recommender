# FPL Recommender

A lightweight **Fantasy Premier League** recommender: Python ranking core + a tiny **FastAPI** backend with a built-in browser UI.

## Features
- Pulls live player and fixture data from the official FPL API  
- Composite scoring: form, points-per-game, short-term fixture outlook, ICT index, availability penalty  
- Respects budget + “max 3 from a team”  
- CLI **and** REST API with a minimal UI  
- CSV export (`/recommend.csv`)  
- Simple 15-minute on-disk cache

## Quickstart (Windows)

```bat
:: create and activate venv
python -m venv .venv
.venv\Scripts\activate

:: install deps
pip install -r requirements.txt
```

### CLI example
```bat
python -m src.fpl_recommender.app --budget 12.5 --need "2:1,3:1" --exclude "" --max-from-team 3
```

### Run the API + UI
```bat
uvicorn src.fpl_recommender.server:app --reload
```

Open:
- UI: http://127.0.0.1:8000/
- Swagger: http://127.0.0.1:8000/docs

### Useful endpoints
- `GET /health` → `{ "ok": true }`  
- `GET /recommend?budget=12.5&need=2:1,3:1&exclude=&max_from_team=3&top_per_pos=30`  
- `GET /recommend.csv?budget=12.5&need=2:1,3:1` → download CSV  
- `GET /search?query=palmer` → quick ID lookup by player name

**Param notes**
- `need` uses FPL element types: `1=GK, 2=DEF, 3=MID, 4=FWD` (example: `2:1,3:1` means 1 DEF and 1 MID)  
- `max_from_team` is capped at 3 to match FPL rules  
- `top_per_pos` trims candidate pools for speed (default 30)

## How scoring works (short version)
Each player gets a composite **score**:
```
score =
  0.40 * form
+ 0.25 * points_per_game
+ 0.20 * fixture_outlook        (next ~3 fixtures, easier = higher)
+ 0.10 * ict_index
+ 0.05 * expected_points        (if present in API)
+ risk_penalty                  (minutes risk; only negative)
```
Unavailable/out players are filtered; “doubtful” are penalized rather than dropped. Output also shows a handy **value** column (`points_per_game / price`) to spot bargains.

## Project layout
```
src/fpl_recommender/
  data/fpl_api.py         # FPL API fetchers
  features/ranker.py      # scoring + recommenders
  utils/cache.py          # tiny disk cache (15 min TTL)
  app.py                  # CLI
  server.py               # FastAPI + minimal UI
tests/
  test_server.py          # /health smoke test
```

## Development
```bat
:: run API on a different port (optional)
uvicorn src.fpl_recommender.server:app --reload --port 8010

:: run tests
pytest -q
```

## Roadmap
- Fixture horizon & weight knobs exposed via API/UI  
- Smarter selection (small combo solver / ILP)  
- Import current squad to auto-exclude + adjust budget

## License
MIT — see `LICENSE` (or `LICENSE.txt`).
