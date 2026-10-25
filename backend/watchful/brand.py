"""The product's name and contact details, read once from brand.json at the repo root; never written as literals."""
import json
from dataclasses import dataclass, fields
from pathlib import Path

BRAND_FILE = Path(__file__).resolve().parents[2] / "brand.json"


@dataclass(frozen=True)
class Brand:
    name: str
    tagline: str
    domain: str
    contact_email: str
    theme_color: str
    description: str = ""
    operator: str = ""
    country: str = ""


def load_brand(path: Path = BRAND_FILE) -> Brand:
    data = json.loads(path.read_text())
    known = {f.name for f in fields(Brand)}
    return Brand(**{k: v for k, v in data.items() if k in known})


BRAND = load_brand()
