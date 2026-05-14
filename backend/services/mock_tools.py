"""
services/mock_tools.py — Deterministic mock data sources for the demo.

All tool functions return fixed data matching the scenario spec exactly.
maybe_update_memory() is a lightweight regex-based extractor that populates
StructuredMemory fields from each user utterance — no NLP library needed.
"""
from __future__ import annotations

import re
from typing import Optional

# ── Mock data ─────────────────────────────────────────────────────────────────

_ORDER_DB: dict[tuple, dict] = {
    ("4421", "rahul@example.com"): {
        "status": "out for delivery",
        "estimated_delivery": "tomorrow",
        "tracking_link": "https://example.com/track/4421",
        "refund_policy": (
            "Refund available if delivery fails after the promised date. "
            "Contact support within 48 hours of the missed delivery."
        ),
    }
}

_HOTEL_OPTIONS: list[dict] = [
    {"name": "Comfort Stay Indiranagar",  "price": 4500, "area": "Indiranagar"},
    {"name": "Metro Grand Koramangala",   "price": 4900, "area": "Koramangala"},
    {"name": "Garden Nest Whitefield",    "price": 4200, "area": "Whitefield"},
]

_WEATHER_DB: dict[str, dict] = {
    "mumbai":    {"condition": "warm and humid",   "temp_c": 32},
    "delhi":     {"condition": "hot and dry",      "temp_c": 36},
    "chennai":   {"condition": "warm and breezy",  "temp_c": 33},
    "bangalore": {"condition": "pleasant",         "temp_c": 28},
    "hyderabad": {"condition": "hot and sunny",    "temp_c": 35},
}

# ── Tool functions ────────────────────────────────────────────────────────────

def lookup_order(order_id: str, email: str) -> Optional[dict]:
    """Return order details for a matching (order_id, email) pair, or None."""
    key = (str(order_id).strip(), email.strip().lower())
    return _ORDER_DB.get(key)


def search_hotels(city: str, dates: str, people: int, budget: int) -> list[dict]:
    """Return hotel options that fit within the given budget."""
    return [h for h in _HOTEL_OPTIONS if h["price"] <= budget]


def get_weather(city: str) -> Optional[dict]:
    """Return weather data for a known city, or None."""
    return _WEATHER_DB.get(city.strip().lower())


# ── Regex patterns ────────────────────────────────────────────────────────────

_ORDER_ID_RE = re.compile(r"\b(4421|\d{4,6})\b")
_EMAIL_RE    = re.compile(r"[\w.+\-]+@[\w\-]+\.[a-z]{2,}", re.IGNORECASE)
_CITY_RE     = re.compile(
    r"\b(mumbai|delhi|chennai|bangalore|hyderabad)\b", re.IGNORECASE
)
_HOTEL_SELECT_RE = re.compile(
    r"\b(first|second|third|1st|2nd|3rd|option\s*1|option\s*2|option\s*3"
    r"|number\s*1|number\s*2|number\s*3)\b",
    re.IGNORECASE,
)
_HOTEL_IDX_MAP: dict[str, int] = {
    "first": 0, "1st": 0, "option 1": 0, "number 1": 0,
    "second": 1, "2nd": 1, "option 2": 1, "number 2": 1,
    "third":  2, "3rd": 2, "option 3": 2, "number 3": 2,
}


# ── Memory updater ────────────────────────────────────────────────────────────

def maybe_update_memory(text: str, memory) -> None:
    """
    Regex-based memory field extractor. Runs after every user utterance.
    Updates memory.structured in-place; does not return anything.
    """
    t_lower = text.lower()
    mem = memory.structured

    # ── Order ID ──────────────────────────────────────────────────────────
    m = _ORDER_ID_RE.search(text)
    if m:
        mem.order_id = m.group(1)

    # ── Email ─────────────────────────────────────────────────────────────
    m = _EMAIL_RE.search(text)
    if m:
        mem.email = m.group(0).lower()

    # ── Auto-lookup order when both fields are present ────────────────────
    if mem.order_id and mem.email:
        order = lookup_order(mem.order_id, mem.email)
        if order and mem.order_status is None:
            mem.order_status        = order["status"]
            mem.estimated_delivery  = order["estimated_delivery"]
            mem.tracking_link       = order["tracking_link"]
            mem.refund_policy       = order["refund_policy"]
            mem.last_topic          = "order"

    # ── City / weather ────────────────────────────────────────────────────
    cities_found = _CITY_RE.findall(text)
    if cities_found:
        if mem.weather_cities is None:
            mem.weather_cities = []
        for c in cities_found:
            c_l = c.lower()
            if c_l not in [x.lower() for x in mem.weather_cities]:
                mem.weather_cities.append(c_l)
        if not mem.last_topic or mem.last_topic == "weather":
            mem.last_topic = "weather"

    # ── Hotel city ────────────────────────────────────────────────────────
    if "bangalore" in t_lower:
        mem.hotel_city = "Bangalore"
        if not mem.last_topic:
            mem.last_topic = "hotel"

    # ── Hotel dates ───────────────────────────────────────────────────────
    if "next weekend" in t_lower or "próximo fin de semana" in t_lower:
        mem.hotel_dates = "next weekend"

    # ── Hotel people ──────────────────────────────────────────────────────
    m = re.search(r"\b(two|dos|2)\s*(people|persons|personas)?\b", t_lower)
    if m:
        mem.hotel_people = 2

    # ── Hotel budget (INR, 1000–20000 range) ──────────────────────────────
    m = re.search(r"\b(\d{4,5})\s*(?:rupees?|inr|rupias?|per\s*night)?\b", t_lower)
    if m:
        val = int(m.group(1))
        if 1000 <= val <= 20000:
            mem.hotel_budget = val

    # ── Populate hotel options once city + budget are known ───────────────
    if mem.hotel_city and mem.hotel_budget and mem.hotel_options is None:
        mem.hotel_options = search_hotels(
            mem.hotel_city,
            mem.hotel_dates or "next weekend",
            mem.hotel_people or 2,
            mem.hotel_budget,
        )
        if mem.hotel_options:
            mem.last_topic = "hotel"

    # ── Hotel selection ───────────────────────────────────────────────────
    m = _HOTEL_SELECT_RE.search(text)
    if m and mem.hotel_options:
        sel_key = m.group(1).lower().replace("  ", " ")
        for pattern, idx in _HOTEL_IDX_MAP.items():
            if pattern in sel_key and idx < len(mem.hotel_options):
                mem.selected_hotel_option = mem.hotel_options[idx]["name"]
                break

    # ── Food / pizza ──────────────────────────────────────────────────────
    if "pizza" in t_lower or "food" in t_lower:
        mem.last_topic = "food"
