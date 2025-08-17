from typing import Dict, Any, List
import math
import numpy as np
import pandas as pd

# --- Helper scoring functions -------------------------------------------------

def _minutes_risk_penalty(chance_of_playing_next_round: float | None) -> float:
    """Penalty if low chance to play; None/NaN treated as unknown (no penalty)."""
    try:
        # pandas may pass NaN; treat as unknown
        if chance_of_playing_next_round is None or (float(chance_of_playing_next_round) != float(chance_of_playing_next_round)):
            return 0.0
    except Exception:
        return 0.0
    # scale: 0 -> heavy penalty, 100 -> none
    return - max(0.0, (60 - float(chance_of_playing_next_round))) / 60.0


def _fixture_score(row: pd.Series, fixtures_df: pd.DataFrame, team_strengths: dict, horizon: int = 3) -> float:
    """
    Look ahead N fixtures: easier fixtures (low opponent strength, low FDR) score higher.
    """
    team_id = row["team"]
    # filter upcoming fixtures for this team
    upcoming = fixtures_df[(~fixtures_df["finished"]) & ((fixtures_df["team_h"] == team_id) | (fixtures_df["team_a"] == team_id))].head(horizon)

    if upcoming.empty:
        return 0.0

    scores = []
    for _, fx in upcoming.iterrows():
        is_home = fx["team_h"] == team_id
        opp_id = fx["team_a"] if is_home else fx["team_h"]
        # FDR 1-5 where 1 is easiest; invert to make higher better
        fdr = fx["team_h_difficulty"] if is_home else fx["team_a_difficulty"]
        inv_fdr = (6 - fdr)

        # opponent defensive strength proxy (from team_strengths dict 1-5)
        opp_strength = team_strengths.get(opp_id, 3)
        inv_opp = (6 - opp_strength)

        # small home boost
        home_boost = 0.2 if is_home else 0.0

        scores.append(inv_fdr * 0.6 + inv_opp * 0.3 + home_boost)

    return float(np.mean(scores))

def build_player_frame(bootstrap: Dict[str, Any], fixtures: List[Dict[str, Any]]) -> pd.DataFrame:
    elements = pd.DataFrame(bootstrap.get("elements", []))
    teams = pd.DataFrame(bootstrap.get("teams", []))
    element_types = pd.DataFrame(bootstrap.get("element_types", []))
    fixtures_df = pd.DataFrame(fixtures or [])

    # ---- Safe maps for position / team names ----
    et_map = {}
    if not element_types.empty and "id" in element_types.columns and "singular_name_short" in element_types.columns:
        et_map = dict(zip(element_types["id"], element_types["singular_name_short"]))
    if not et_map:
        et_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

    team_name_map = {}
    if not teams.empty and "id" in teams.columns and "name" in teams.columns:
        team_name_map = dict(zip(teams["id"], teams["name"]))

    # ---- Safe team strengths -> normalized 1..5 scale ----
    # Fill missing strength cols with neutral 3 to avoid KeyErrors
    for col in ["strength_overall_home", "strength_overall_away"]:
        if col not in teams.columns:
            teams[col] = 3
    if "id" not in teams.columns:
        teams["id"] = range(1, len(teams) + 1)

    raw_strength = (teams["strength_overall_home"] + teams["strength_overall_away"]) / 2
    team_strengths = dict(zip(teams["id"], raw_strength))
    if len(team_strengths) > 0:
        s_vals = list(team_strengths.values())
        lo, hi = min(s_vals), max(s_vals)
        for k, v in list(team_strengths.items()):
            if hi == lo:
                team_strengths[k] = 3
            else:
                team_strengths[k] = 1 + 4 * ((float(v) - float(lo)) / (float(hi) - float(lo)))
    else:
        team_strengths = {}

    # ---- Columns we want, with safe defaults when missing ----
    desired_cols = {
        "id": None,
        "web_name": "",
        "team": 0,
        "element_type": 0,
        "now_cost": 0,
        "form": 0.0,
        "points_per_game": 0.0,
        "selected_by_percent": 0.0,
        "minutes": 0,
        "chance_of_playing_next_round": None,   # may be absent; treat as unknown
        "status": "a",
        "expected_points": 0.0,                 # often missing in bootstrap
        "ict_index": 0.0,
        "value_season": 0.0,
        "goals_scored": 0,
        "assists": 0,
        "clean_sheets": 0,
    }
    for c, default in desired_cols.items():
        if c not in elements.columns:
            elements[c] = default

    df = elements[list(desired_cols.keys())].copy()

    # ---- Basic transforms ----
    df["price"] = pd.to_numeric(df["now_cost"], errors="coerce").fillna(0) / 10.0
    for c in ["form", "points_per_game", "selected_by_percent", "expected_points", "ict_index", "value_season"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    df["pos"] = df["element_type"].map(et_map)
    df["team_name"] = df["team"].map(team_name_map).fillna("")
    df["risk_penalty"] = df["chance_of_playing_next_round"].apply(_minutes_risk_penalty)

    # ---- Fixture outlook (safe) ----
    # Ensure required columns exist; otherwise skip and use 0.0
    fixtures_ok = False
    if not fixtures_df.empty:
        required = {"team_h", "team_a", "team_h_difficulty", "team_a_difficulty"}
        fixtures_ok = required.issubset(set(fixtures_df.columns))
        if "finished" not in fixtures_df.columns:
            fixtures_df["finished"] = False

    if fixtures_ok:
        df["fixture_outlook"] = df.apply(
            _fixture_score,
            axis=1,
            fixtures_df=fixtures_df,
            team_strengths=team_strengths,
            horizon=3,
        )
    else:
        df["fixture_outlook"] = 0.0

    # ---- Composite score (tunable weights) ----
    df["score"] = (
        0.40 * df["form"] +
        0.25 * df["points_per_game"] +
        0.20 * df["fixture_outlook"] +
        0.10 * df["ict_index"] +
        0.05 * df["expected_points"] +
        df["risk_penalty"]
    )

    # Keep available/doubtful only (doubtful is penalized by risk_penalty)
    df = df[df["status"].isin(["a", "d"])]

    return df.sort_values("score", ascending=False).reset_index(drop=True)


def recommend(df: pd.DataFrame,
              budget: float,
              need_positions: Dict[int, int],
              exclude_ids: set,
              max_from_team: int = 3,
              top_k_per_pos: int = 30) -> pd.DataFrame:
    """
    Greedy fill per position bucket under budget and max_from_team rule.
    """
    result_rows = []
    spend = 0.0
    team_counts: Dict[int, int] = {}

    # Pre-filter: exclude current squad, and take a reasonable top slice per position to keep it fast
    short = (
        df[~df["id"].isin(list(exclude_ids))]
        .sort_values("score", ascending=False)
    )

    for pos_id, count_needed in need_positions.items():
        # map to short code from element_type id -> pos label already in df["pos"]
        pos_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
        pos_label = pos_map[pos_id]

        candidates = short[short["pos"] == pos_label].head(top_k_per_pos).copy()

        # Greedy: iterate sorted by score, pick subject to budget + team cap
        for _, row in candidates.iterrows():
            if count_needed <= 0:
                break
            t = int(row["team"])
            price = float(row["price"])

            if spend + price > budget:
                continue
            if team_counts.get(t, 0) >= max_from_team:
                continue

            result_rows.append(row)
            spend += price
            team_counts[t] = team_counts.get(t, 0) + 1
            count_needed -= 1

    rec_df = pd.DataFrame(result_rows).copy()
    if not rec_df.empty:
        rec_df["cum_spend"] = rec_df["price"].cumsum()
    return rec_df
