import dataclasses
import json

import pytest

from watchful.brand import BRAND, BRAND_FILE, load_brand


def test_brand_comes_from_brand_json():
    data = json.loads(BRAND_FILE.read_text())
    assert BRAND.name == data["name"] and BRAND.domain == data["domain"]
    assert BRAND.theme_color.startswith("#") and "@" in BRAND.contact_email
    with pytest.raises(dataclasses.FrozenInstanceError):
        BRAND.name = "x"


def test_unknown_keys_are_ignored(tmp_path):
    f = tmp_path / "brand.json"
    f.write_text(json.dumps({"name": "N", "tagline": "t", "domain": "d", "contact_email": "a@b", "theme_color": "#000",
                             "logo_text": "extra"}))
    assert load_brand(f).name == "N"
