import argparse
import json
from typing import Set, Dict
import pandas as pd

from .data.fpl_api import fetch_bootstrap_static, fetch_fixtures, throttle
from .features.ranker import build_player_frame, recommend
from .utils.rules import SquadConstraints

def parse_exclude_ids(s: str) -> Set[int]:
    """
    Accepts comma-separated player IDs or an empty string.
    """
    if not s:
        return set()
    return set(int(x.strip()) for x in s.split(",") if x.strip())

def parse_positions(s: str) -> Dict[int, int]:
    """
    Example: "2:1,3:2,4:1" meaning 1 DEF, 2 MID, 1 FWD to add.
    Element types: 1=GK, 2=DEF, 3=MID, 4=FWD
    """
    out: Dict[int, int] = {}
    if not s:
        return out
    for chunk in s.split(","):
        k, v = chunk.split(":")
        out[int(k)] = int(v)
    return out

def main():
    parser = argparse.ArgumentParser(description="FPL Recommender CLI")
    parser.add_argument("--budget", type=float, required=True, help="Total budget in £m (e.g., 7.5)")
    parser.add_argument("--need", type=str, required=True, help='Positions to add as "1:0,2:1,3:2,4:1" for GK/DEF/MID/FWD')
    parser.add_argument("--exclude", type=str, default="", help="Comma-separated current squad player IDs to exclude")
    parser.add_argument("--max-from-team", type=int, default=3, help="Max players from one team (default 3)")
    parser.add_argument("--top-per-pos", type=int, default=30, help="Top slice per position to consider (speed tradeoff)")

    args = parser.parse_args()
    exclude_ids = parse_exclude_ids(args.exclude)
    need_positions = parse_positions(args.need)

    print("Fetching FPL data…")
    bootstrap = fetch_bootstrap_static()
    throttle(0.4)
    fixtures = fetch_fixtures()

    print("Building player table…")
    df = build_player_frame(bootstrap, fixtures)

    print("Scoring & recommending…")
    rec = recommend(
        df=df,
        budget=args.budget,
        need_positions=need_positions,
        exclude_ids=exclude_ids,
        max_from_team=args.max_from_team,
        top_k_per_pos=args.top_per_pos
    )

    if rec.empty:
        print("No valid recommendations under the given constraints.")
        return

    # Pretty print essentials (rounded) + simple value metric
    cols = ["id","web_name","team_name","pos","price","score","form","points_per_game","fixture_outlook","chance_of_playing_next_round"]
    pretty = rec[cols].copy()
    pretty["value"] = (pretty["points_per_game"] / pretty["price"]).replace([float("inf"), -float("inf")], 0).fillna(0).round(3)
    for c in ["price","score","form","points_per_game","fixture_outlook"]:
        pretty[c] = pd.to_numeric(pretty[c], errors="coerce").fillna(0).round(2)
    print(pretty.to_string(index=False))


    # Also drop a machine-friendly JSON file
    out_path = "recommendations.json"
    rec.to_json(out_path, orient="records", indent=2)
    print(f"\nSaved JSON to {out_path}")

if __name__ == "__main__":
    main()
