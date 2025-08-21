# src/fpl_recommender/server.py
from __future__ import annotations

import csv
import io
import inspect
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Sequence

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

# ----------------------------- Logging ---------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("fpl.server")

# -------------------------- Your recommender ---------------------------------
try:
    from src.fpl_recommender.features.ranker import recommend  # type: ignore
    log.info("Using recommender: src.fpl_recommender.features.ranker.recommend")
except Exception as e:
    recommend = None  # type: ignore
    log.error("Failed to import recommender: %s", e)

# ----------------------------- Data loader -----------------------------------
def _build_df_from_fpl_api():
    import pandas as pd  # defer import
    from src.fpl_recommender.data.fpl_api import fetch_bootstrap_static, fetch_fixtures, throttle

    log.info("Fetching bootstrap-static …")
    boot = fetch_bootstrap_static()
    throttle(0.2)
    log.info("Fetching fixtures …")
    _ = fetch_fixtures()  # not required for columns we add here

    elements = boot.get("elements", [])
    teams = boot.get("teams", [])

    df = pd.DataFrame(elements)

    if "id" not in df.columns:
        raise RuntimeError("Loader failed: DataFrame missing required 'id' column")

    # ----- Position columns -----
    pos_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    if "element_type" in df.columns:
        df["position_id"] = df["element_type"]
        df["pos"] = df["element_type"].map(pos_map).fillna("UNK")
    else:
        df["position_id"] = 0
        df["pos"] = "UNK"

    # ----- Team short name -----
    if teams and "team" in df.columns:
        team_df = pd.DataFrame(teams)[["id", "short_name", "name"]].rename(
            columns={"id": "team_id", "short_name": "team_short", "name": "team_name"}
        )
        df = df.merge(team_df, left_on="team", right_on="team_id", how="left") \
               .drop(columns=[c for c in ("team_id",) if c in df.columns])

    # ----- Player name helpers (nice to have) -----
    if "web_name" in df.columns and "second_name" in df.columns:
        df["display_name"] = df["web_name"]
        df["full_name"] = df.get("first_name", "") + " " + df.get("second_name", "")
    elif "web_name" in df.columns:
        df["display_name"] = df["web_name"]

    # ----- Ensure numeric fields + composite score -----
    def _to_float(x):
        try:
            return float(x)
        except Exception:
            return 0.0

    # price expected by ranker; FPL's now_cost is in tenths of a million
    if "price" not in df.columns:
        if "now_cost" in df.columns:
            df["price"] = df["now_cost"].map(_to_float) / 10.0
        else:
            df["price"] = 0.0

    if "score" not in df.columns:
        form = df["form"].map(_to_float) if "form" in df.columns else 0.0
        ppg = df["points_per_game"].map(_to_float) if "points_per_game" in df.columns else 0.0
        ict = df["ict_index"].map(_to_float) if "ict_index" in df.columns else 0.0
        df["score"] = (0.45 * ppg) + (0.35 * form) + (0.20 * (ict / 10.0))

    log.info("Loaded players df: %s rows, %s cols", len(df), len(df.columns))
    return df


@lru_cache(maxsize=1)
def _get_df():
    return _build_df_from_fpl_api()


def _reload_df():
    _get_df.cache_clear()  # type: ignore[attr-defined]
    _get_df()


# ---------------------------- FastAPI + Static UI ----------------------------
app = FastAPI(title="FPL Recommender", version="1.9")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

HERE = Path(__file__).resolve()
SRC_DIR = HERE.parent
REPO_ROOT = SRC_DIR.parent.parent if SRC_DIR.name == "fpl_recommender" else SRC_DIR.parent
STATIC_DIR = REPO_ROOT / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR, html=True), name="static")


@app.get("/", include_in_schema=False)
def ui_root() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Missing UI: create 'static/index.html' at the repo root.")
    return FileResponse(index)


@app.get("/health", include_in_schema=False)
def health() -> Dict[str, Any]:
    ok_df = True
    try:
        df = _get_df()
        ok_df = df is not None
    except Exception:
        ok_df = False
    return {
        "ok": True,
        "ui": (STATIC_DIR / "index.html").exists(),
        "recommender_found": bool(recommend),
        "df_loaded": ok_df,
    }


@app.post("/reload-data", include_in_schema=False)
def reload_data() -> Dict[str, Any]:
    try:
        _reload_df()
        return {"ok": True, "reloaded": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reload failed: {e}")


# --------------------------------- Helpers -----------------------------------
def _parse_positions(need: str) -> Dict[int, int]:
    need = (need or "").replace(";", ",").strip()
    if not need:
        return {}
    out: Dict[int, int] = {}
    parts = [p.strip() for p in need.split(",") if p.strip()]
    for p in parts:
        if ":" not in p:
            try:
                pos = int(p)
                out[pos] = out.get(pos, 0) + 1
            except ValueError:
                continue
            continue
        k, v = p.split(":", 1)
        try:
            pos = int(k.strip())
            count = int(float(v.strip()))
            if count > 0:
                out[pos] = out.get(pos, 0) + count
        except ValueError:
            continue
    return out


def _parse_ids(exclude: str) -> List[int]:
    if not exclude:
        return []
    exclude = exclude.replace(";", ",")
    ids = []
    for tok in exclude.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            ids.append(int(tok))
        except ValueError:
            continue
    return ids


def _normalize_rows(obj) -> List[Dict[str, Any]]:
    if isinstance(obj, list) and (not obj or isinstance(obj[0], dict)):
        return obj
    try:
        import pandas as pd  # type: ignore
        if isinstance(obj, pd.DataFrame):  # type: ignore
            return obj.to_dict(orient="records")  # type: ignore
    except Exception:
        pass
    if isinstance(obj, tuple) and obj:
        return _normalize_rows(obj[0])
    if obj is None:
        return []
    return [{"value": str(obj)}]


def _to_csv(rows: Sequence[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    cols = list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in cols})
    return buf.getvalue()


def _wants_csv(request: Request, fmt: str | None) -> bool:
    if fmt and fmt.lower() == "csv":
        return True
    accept = request.headers.get("accept", "")
    return "text/csv" in accept.lower()


# --------- Build kwargs dynamically to match your recommender signature -------
_ALIAS_GROUPS = {
    "df": ["df", "data", "players_df", "players", "dataset"],
    "budget": ["budget", "total_budget"],
    "need_positions": ["need_positions", "need", "positions_needed", "positions"],
    "exclude_ids": ["exclude_ids", "exclude", "exclude_list", "exclude_players"],
    "max_from_team": ["max_from_team", "max_per_team", "team_limit"],
    # only passed if present in your recommender signature
    "top_per_pos": ["top_per_pos", "top_per_position", "top_n_per_pos", "top_n", "top_k", "limit"],
}

import math
from typing import Mapping

def _json_sanitize(obj):
    """Recursively convert NaN/Inf -> None and numpy/pandas scalars -> py scalars."""
    # numpy / pandas scalars
    try:
        import numpy as np  # type: ignore
        if isinstance(obj, np.generic):  # type: ignore
            obj = obj.item()
    except Exception:
        pass

    # primitives
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if obj is None or isinstance(obj, (int, str, bool)):
        return obj

    # containers
    if isinstance(obj, Mapping):
        return {k: _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_sanitize(v) for v in obj]

    # everything else -> string
    return str(obj)

# --- add near other helpers ---
def _compact_row(r: Dict[str, Any]) -> Dict[str, Any]:
    """Project a raw row to the minimal fields we want for the UI."""
    # names
    name = r.get("display_name") or r.get("web_name") or r.get("second_name") or r.get("full_name") or r.get("name")

    # team / position
    team = r.get("team_short") or r.get("team_name") or r.get("team")
    pos  = r.get("pos") or r.get("position") or r.get("position_name")

    # numbers
    def f(x, dp=2):
        try:
            return round(float(x), dp)
        except Exception:
            return None

    return {
        "name": name,
        "team": team,
        "pos": pos,
        "price": f(r.get("price"), 1),
        "form": f(r.get("form")),
        "ppg": f(r.get("points_per_game")),
        "score": f(r.get("score")),
        # keep raw id if present (handy for debugging/links; hidden in UI)
        "id": r.get("id"),
    }

def _adapt_and_call_recommend(df, budget, need_positions, exclude_ids, max_from_team, top_per_pos):
    if recommend is None:
        raise RuntimeError("Recommender not found")

    params = set(inspect.signature(recommend).parameters.keys())  # type: ignore[arg-type]

    raw_values = {
        "df": df,
        "budget": budget,
        "need_positions": need_positions,
        "exclude_ids": exclude_ids,
        "max_from_team": max_from_team,
        "top_per_pos": top_per_pos,
    }

    kwargs: Dict[str, Any] = {}
    used_map: Dict[str, str] = {}

    for canonical, aliases in _ALIAS_GROUPS.items():
        for name in aliases:
            if name in params:
                kwargs[name] = raw_values[canonical]
                used_map[canonical] = name
                break

    log.info("Param map: %s", ", ".join(f"{k}->{v}" for k, v in used_map.items()) or "None")
    return recommend(**kwargs)  # type: ignore[misc]


# ---------------------------------- API --------------------------------------
@app.get("/recommend")
def api_recommend(
    request: Request,
    budget: float = Query(...),
    need: str = Query(...),
    exclude: str = Query(""),
    max_from_team: int = Query(3, ge=1, le=3),
    top_per_pos: int = Query(30, ge=1, le=100),
    format: str | None = Query(None),
    compact: bool = Query(True, description="Return compact fields for UI"),   # <— NEW
):

    if recommend is None:
        raise HTTPException(status_code=500, detail="Recommender function not found.")

    df = _get_df()
    if df is None:
        raise HTTPException(status_code=500, detail="Dataframe not loaded.")

    need_positions = _parse_positions(need)
    exclude_ids = _parse_ids(exclude)

    try:
        raw = _adapt_and_call_recommend(
            df=df,
            budget=budget,
            need_positions=need_positions,
            exclude_ids=exclude_ids,
            max_from_team=max_from_team,
            top_per_pos=top_per_pos,
        )
        rows = _normalize_rows(raw)
        if compact:
            rows = [_compact_row(r) for r in rows]

        log.info("Generated %d rows", len(rows))
    except Exception as e:
        log.exception("Recommendation error")
        raise HTTPException(status_code=500, detail=f"Recommendation error: {e}") from e

    if _wants_csv(request, format):
        csv_text = _to_csv(rows)
        return PlainTextResponse(
            content=csv_text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="recommendations.csv"'},
        )

    return JSONResponse(_json_sanitize({"items": rows}))
