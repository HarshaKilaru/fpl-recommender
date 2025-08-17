from dataclasses import dataclass
from typing import List, Set, Dict

# FPL constraints
MAX_FROM_TEAM = 3
SQUAD_SIZE = 15

# positions per FPL element_type
# 1=GK, 2=DEF, 3=MID, 4=FWD
POSITION_NAMES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

@dataclass
class SquadConstraints:
    budget: float                      # total £ available to spend (e.g., 6.5)
    need_positions: Dict[int, int]     # {element_type: count_to_add}
    exclude_ids: Set[int]              # players already in your team
    max_from_team: int = MAX_FROM_TEAM
