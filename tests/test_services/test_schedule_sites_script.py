"""scripts/schedule_sites.py: crons, slots and the commands it prints."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "schedule_sites.py"
SPEC = importlib.util.spec_from_file_location("schedule_sites", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_slots_become_weekly_crons_in_lisbon_time():
    assert mod.cron_for("entreparedes") == "0 1 * * 0"   # Sunday 01:00
    assert mod.cron_for("habita") == "30 2 * * 0"
    assert mod.cron_for("realkey") == "0 7 * * 1"        # Monday 07:00
    assert mod.cron_for("t2mais1") == "15 6 * * 1"


def test_every_slot_is_unique_and_big_sites_have_an_hour_to_themselves():
    times = sorted(mod.slot_datetime(*slot) for slot in mod.SLOTS.values())
    assert len(set(times)) == len(times)
    big = ["entreparedes", "habita", "barcelcasa", "brightmangroup_pt", "casa10_pt"]
    starts = [mod.slot_datetime(*mod.SLOTS[k]) for k in big]
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(starts, starts[1:])]
    assert all(g >= 60 for g in gaps)


def test_start_url_goes_to_the_sitemap_or_the_first_page():
    sitemap = {"base_url": "https://www.habita-imoveis.pt", "pagination_type": "sitemap",
               "schedule_start_url": "https://www.habita-imoveis.pt/comprar-casa/x"}
    assert mod.start_url_for(sitemap) == "https://www.habita-imoveis.pt/sitemap.xml"
    paged = {"base_url": "https://www.habinedita.com", "pagination_type": "html_next",
             "schedule_start_url": "https://www.habinedita.com/imoveis/?pg=2&o=1"}
    assert mod.start_url_for(paged) == "https://www.habinedita.com/imoveis/?pg=1&o=1"


def test_scheduler_command_targets_the_trigger_and_hides_the_key():
    args = mod.scheduler_args("create", "t2mais1", "https://api.example.run.app", "TOPSECRET", "proj", "europe-west1")
    assert args[:5] == ["scheduler", "jobs", "create", "http", "weekly-t2mais1"]
    assert "https://api.example.run.app/api/v1/jobs/trigger/t2mais1" in args
    assert args[args.index("--schedule") + 1] == "15 6 * * 1"
    assert "--headers" in args and "X-API-Key=TOPSECRET" in args
    shown = mod.printable(args)
    assert "TOPSECRET" not in shown and "X-API-Key=***" in shown


def test_updating_an_existing_job_replaces_headers_with_update_headers():
    args = mod.scheduler_args("update", "t2mais1", "https://api.example.run.app", "k", "proj", "europe-west1")
    assert "--update-headers" in args and "--headers" not in args
