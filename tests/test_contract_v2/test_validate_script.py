"""scripts/validate_contract.py: pages through the feed, validates every item and fails on violations."""
import importlib.util
from pathlib import Path

import pytest

from app.contract_v2 import to_contract
from tests.test_contract_v2.fixtures import PARTNER, partner_listings

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_contract.py"
SPEC = importlib.util.spec_from_file_location("validate_contract", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

SCHEMA = __import__("json").loads(
    (Path(__file__).resolve().parents[2] / "app" / "contracts" / "listing-2.0.schema.json").read_text(encoding="utf-8")
)


def _fake_api(items, page_size=4):
    """A get() that serves `items` in pages, like /api/v2/listings."""
    def get(url, key):
        if url.endswith("/schemas/listing/2.0.json"):
            return SCHEMA
        assert key == "k", "the feed needs the API key"
        offset = int(url.split("cursor=")[1].split("&")[0]) if "cursor=" in url else 0
        chunk = items[offset:offset + page_size]
        more = offset + page_size < len(items)
        return {"items": chunk, "next_cursor": str(offset + page_size) if more else None, "total": len(items)}
    return get


def _run(monkeypatch, capsys, items):
    monkeypatch.setenv("MVP_API_URL", "https://h.run.app/api/v1")
    monkeypatch.setenv("MVP_API_KEY", "k")
    monkeypatch.setattr("sys.argv", ["validate_contract.py", "--limit", "4"])
    monkeypatch.setattr(mod, "get", _fake_api(items))
    with pytest.raises(SystemExit) as stop:
        mod.main()
    return stop.value.code, capsys.readouterr().out


def test_v2_root_is_derived_from_the_v1_url():
    assert mod.v2_root("https://h.run.app/api/v1") == "https://h.run.app/api/v2"
    assert mod.v2_root("https://h.run.app/api/v1/") == "https://h.run.app/api/v2"
    assert mod.v2_root("http://localhost:8000") == "http://localhost:8000/api/v2"


def test_clean_feed_exits_zero_and_lists_every_partner(monkeypatch, capsys):
    items = [to_contract(listing, {**PARTNER, "key": key}) for key, listing in partner_listings().items()]
    code, out = _run(monkeypatch, capsys, items)
    assert code == 0
    assert "14 listings, 0 violating the schema, 0 repeated" in out
    assert all(key in out for key in partner_listings())


def test_a_violation_is_reported_and_fails_the_run(monkeypatch, capsys):
    items = [to_contract(listing, PARTNER) for listing in list(partner_listings().values())[:5]]
    items[2]["price"]["currency"] = "XYZ"
    code, out = _run(monkeypatch, capsys, items)
    assert code == 1
    assert "1 violating the schema" in out and "price/currency" in out


def test_an_item_repeated_across_pages_fails_the_run(monkeypatch, capsys):
    item = to_contract(partner_listings()["realkey"], PARTNER)
    code, out = _run(monkeypatch, capsys, [item] * 2)
    assert code == 1 and "1 repeated across pages" in out
