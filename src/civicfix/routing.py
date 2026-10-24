from __future__ import annotations

import json
from pathlib import Path

from civicfix.models import RouteRecommendation


ROUTES_FILE = Path(__file__).resolve().parents[2] / "config" / "routes.json"


def recommendations(category: str) -> tuple[str, list[RouteRecommendation]]:
    with ROUTES_FILE.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    city = config["jurisdiction"]
    entries = config.get("category_routes", {}).get(category, config["default_routes"])
    return city, [RouteRecommendation.model_validate(entry) for entry in entries]
