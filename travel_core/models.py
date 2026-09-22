"""Immutable search state; no mutable global search state."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ItineraryState:
    current_destination_id: str
    access_id: str
    destination_arrival_at: datetime
    first_departure_at: datetime
    visited_destination_ids: frozenset[str]
    remaining_required_ids: frozenset[str]
    optional_count: int
    flight_total_minor: int
    offer_ids: tuple[str, ...]
    access_ids: tuple[str, ...]


SEARCH_CONFIG = {
    "search_version": "exact-dfs-v1",
    "mode": "exact",
    "max_expanded_states": None,
    "tie_break_rule": "cost_asc,burden_asc,experience_desc,offers_lex,accesses_lex",
}
