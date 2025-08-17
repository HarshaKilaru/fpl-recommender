from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from typing import Set, Dict
import pandas as pd
import numpy as np
import math
import io, csv

from .data.fpl_api import fetch_bootstrap_static, fetch_fixtures, throttle
from .features.ranker import build_player_frame, recommend
from .utils.cache import cache_json

app = FastAPI(title="FPL Recommender API", version="1.0.0")

# ---------------- Home (minimal UI) ----------------
@app.get("/", response_class=HTMLResponse)
def root():
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>FPL Recommender</title>
<style>
 body{font-family:system-ui,Arial;margin:24px;max-width:1000px}
 .row{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0}
 label{display:flex;flex-direction:column;font-size:12px}
 input{padding:8px 10px;font-size:14px}
 button{padding:10px 14px;font-size:14px;cursor:pointer}
 table{border-collapse:collapse;width:100%;margin-top:16px}
 th,td{border:1px solid #ddd;padding:8px;font-size:14px;text-align:left}
 th{background:#f3f4f6}
 .hint{font-size:12px;color:#666;margin-top:4px}
 .meta{margin-top:10px;color:#333}
 .err{color:#b91c1c;margin-top:10px}
 .actions{display:flex;gap:10px;margin-top:10px}
</style></head>
<body>
<h1>FPL Recommender</h1>
<div class="row">
  <label>Budget (£m)
    <input id="budget" type="number" step="0.1" value="12.5">
  </label>
  <label>Need (1=GK,2=DEF,3=MID,4=FWD)
    <input id="need" type="text" value="2:1,3:1">
    <div class="hint">Format: 2:1,3:1 → 1 DEF, 1 MID</div>
  </label>
  <label>Exclude IDs (comma-sep)
    <input id="exclude" type="text" placeholder="e.g. 123,456">
  </label>
  <label>Max from team
    <input id="max_from_team" type="number" min="1" max="3" value="3">
  </label>
  <label>Top per position
    <input id="top_per_pos" type="number" min="1" max="100" value="30">
  </label>
</div>
<div class="actions">
  <button id="go">Get recommendations</button>
  <a id="csv" href="#" download="recommendations.csv">Download CSV</a>
</div>
<div id="status" class="meta"></div>
<div id="error" class="err"></div>
<div id="results"></div>
<script>
const el = id => document.getElementById(id);
function currentParams(){
  return new URLSearchParams({
    budget: el("budget").value,
    need: el("need").value,
    exclude: el("exclude").value,
    max_from_team: el("max_from_team").value,
    top_per_pos: el("top_per_pos").value
  });
}
async function fetchRecs() {
  el("error").textContent=""; el("status").textContent="Fetching…"; el("results").innerHTML="";
  const btn=el("go"); btn.disabled=true;
  const params = currentParams();
  el("csv").href = "/recommend.csv?"+params.toString();
  try {
    const res = await fetch("/recommend?"+params.toString());
    const data = await res.json();
    if (!res.ok) throw new Error((data && data.detail) || "Request failed");
    const recs = data.recommendations || [];
    el("status").textContent = `Found ${data.count||recs.length} picks · Spend: £${(data.spent||0).toFixed(2)}m`;
    if (!recs.length){ el("results").innerHTML="<p>No valid recommendations.</p>"; return; }
    const headers=["id","web_name","team_name","pos","price","score","form","points_per_game","fixture_outlook","chance_of_playing_next_round","value"];
    let html="<table><thead><tr>"+headers.map(h=>"<th>"+h.replaceAll("_"," ")+"</th>").join("")+"</tr></thead><tbody>";
    for(const r of recs){ html+="<tr>"+headers.map(h=>`<td>${r[h]??""}</td>`).join("")+"</tr>"; }
    html+="</tbody></table>"; el("results").innerHTML=html;
  } catch(e){ el("error").textContent="Error: "+e.message; }
  finally{ btn.disabled=false; }
}
el("go").addEventListener("click", fetchRecs);
</script>
</body></html>"""

@app.get("/health")
def health():
    return {"ok": True}

# ---------------- Helpers ----------------
def _parse_positions(s: str) -> Dict[int, int]:
    out: Dict[int, int] = {}
    s = (s or "").strip()
    if not s:
        return out
    for chunk in s.split(","):
        k, v = chunk.split(":")
        out[int(k)] = int(v)
    return out

def _parse_ids(s: str) -> Set[int]:
    if not s:
        return set()
    return {int(x.strip()) for x in s.split(",") if x.strip()}

# ---------------- Search players by name ----------------
@app.get("/search")
def search_players(query: str = Query(..., min_length=2)):
    try:
        bootstrap = cache_json("cache_bootstrap.json", 15*60, fetch_bootstrap_static)
        df = build_player_frame(bootstrap, [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {e}")
    q = query.strip().lower()
    sub = df[df["web_name"].str.lower().str.contains(q, na=False)].copy()
    sub = sub[["id","web_name","team_name","pos","price","form","points_per_game"]].head(25)
    for c in ["price","form","points_per_game"]:
        sub[c] = pd.to_numeric(sub[c], errors="coerce").fillna(0).round(2)
    return {"results": sub.to_dict(orient="records")}

# ---------------- Recommend (JSON) ----------------
@app.get("/recommend")
def api_recommend(
    budget: float = Query(..., description="Total budget in £m (e.g., 12.5)"),
    need: str = Query(..., description='Positions like "2:1,3:1" (1=GK,2=DEF,3=MID,4=FWD)'),
    exclude: str = Query("", description="Comma-separated player IDs already in your squad"),
    max_from_team: int = Query(3, ge=1, le=3),
    top_per_pos: int = Query(30, ge=1, le=100),
):
    need_positions = _parse_positions(need)
    exclude_ids = _parse_ids(exclude)

    # Fetch with 15-min disk cache
    try:
        bootstrap = cache_json("cache_bootstrap.json", 15*60, fetch_bootstrap_static)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch FPL bootstrap-static: {e}")
    try:
        throttle(0.2)
        fixtures = cache_json("cache_fixtures.json", 15*60, fetch_fixtures)
    except Exception:
        fixtures = []  # fallback: fixture_outlook -> 0.0

    # Build player frame & compute recommendations
    try:
        df = build_player_frame(bootstrap, fixtures)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build player frame: {e}")

    try:
        rec = recommend(
            df=df,
            budget=budget,
            need_positions=need_positions,
            exclude_ids=exclude_ids,
            max_from_team=max_from_team,
            top_k_per_pos=top_per_pos,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compute recommendations: {e}")

    if rec.empty:
        return {"recommendations": [], "spent": 0.0, "count": 0}

    # ---- JSON-safe response prep ----
    cols = ["id","web_name","team_name","pos","price","score","form","points_per_game","fixture_outlook","chance_of_playing_next_round"]
    for c in cols:
        if c not in rec.columns:
            rec[c] = None
    pretty = rec.loc[:, cols].copy()

    # Coerce numerics & compute 'value'
    pretty["price"] = pd.to_numeric(pretty["price"], errors="coerce")
    pretty["points_per_game"] = pd.to_numeric(pretty["points_per_game"], errors="coerce")
    pretty["score"] = pd.to_numeric(pretty.get("score", 0), errors="coerce")
    pretty["form"] = pd.to_numeric(pretty.get("form", 0), errors="coerce")
    pretty["fixture_outlook"] = pd.to_numeric(pretty.get("fixture_outlook", 0), errors="coerce")

    denom = pretty["price"].replace(0, np.nan)
    pretty["value"] = (pretty["points_per_game"] / denom)

    # Round numerics
    for c in ["price","score","form","points_per_game","fixture_outlook","value"]:
        pretty[c] = pd.to_numeric(pretty[c], errors="coerce").round(2)

    # Hard-sanitize every cell to JSON-safe primitives
    def clean(v):
        try:
            if pd.isna(v):
                return None
        except Exception:
            pass
        if hasattr(v, "item"):
            try:
                v = v.item()
            except Exception:
                pass
        if isinstance(v, (float, int)):
            try:
                if not math.isfinite(float(v)):
                    return None
            except Exception:
                return None
        return v

    recommendations = [{k: clean(v) for k, v in row.items()}
                       for row in pretty.to_dict(orient="records")]

    # Safe spend
    spent_series = pd.to_numeric(rec["price"], errors="coerce")
    spent = float(spent_series.fillna(0).sum())
    if not math.isfinite(spent):
        spent = 0.0
    spent = round(spent, 2)

    return {"recommendations": recommendations, "spent": spent, "count": int(len(recommendations))}

# ---------------- Recommend (CSV download) ----------------
@app.get("/recommend.csv")
def recommend_csv(
    budget: float = Query(...),
    need: str = Query(...),
    exclude: str = Query(""),
    max_from_team: int = Query(3, ge=1, le=3),
    top_per_pos: int = Query(30, ge=1, le=100),
):
    data = api_recommend(budget, need, exclude, max_from_team, top_per_pos)
    recs = data.get("recommendations", [])
    output = io.StringIO()
    fieldnames = ["id","web_name","team_name","pos","price","score","form","points_per_game","fixture_outlook","chance_of_playing_next_round","value"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for r in recs:
        writer.writerow({k: r.get(k, "") for k in fieldnames})
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=recommendations.csv"},
    )
