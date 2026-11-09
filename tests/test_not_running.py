import functools
import http.server
import threading

import httpx
import pytest

from analytics.engine import DealFilters, build_offers, rank_deals
from common import not_running
from dashboard.build import NOT_RUNNING_PATH, _QuietHandler
from tests.conftest import listing


@pytest.fixture
def marks_file(tmp_path, monkeypatch):
    path = tmp_path / "not_running.json"
    monkeypatch.setattr(not_running, "FILE", path)
    return path


@pytest.fixture
def server(marks_file, tmp_path):
    handler = functools.partial(_QuietHandler, directory=str(tmp_path))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_mark_survives_reload_and_can_be_removed(marks_file):
    assert not_running.load() == {}
    not_running.set_mark("m1", "Nike Air Max Dn", True)
    assert not_running.load()["m1"]["name"] == "Nike Air Max Dn"
    not_running.set_mark("m1", "Nike Air Max Dn", False)
    assert not_running.load() == {}


def test_broken_file_means_no_marks(marks_file):
    marks_file.write_text("{ne valja", encoding="utf-8")
    assert not_running.load() == {}


def test_marked_model_leaves_ranking_with_all_its_variants(factory, save, config):
    save(listing())
    save(listing(title="Muške patike ASICS Novablast 5 GTX", sku="G1"))
    with factory() as session:
        offers = build_offers(session, config)
    everything = DealFilters(include_insufficient=True)
    open_config = {**config, "minimum_deal_confidence": "low"}
    assert rank_deals(offers, open_config, everything)
    marked = {**open_config, "not_running_models": [offers[0]["model_id"]]}
    assert rank_deals(offers, marked, everything) == []


def test_api_saves_and_returns_marks(server):
    answer = httpx.post(server + NOT_RUNNING_PATH, json={"model_id": "m1", "name": "Model", "marked": True},
                        headers={"Origin": server})
    assert answer.status_code == 200 and answer.json()["m1"]["name"] == "Model"
    assert "m1" in httpx.get(server + NOT_RUNNING_PATH).json()


@pytest.mark.parametrize("headers", [{"Origin": "https://druga-stranica.test"}, {"Host": "druga-stranica.test"}])
def test_api_refuses_other_sites(server, marks_file, headers):
    answer = httpx.post(server + NOT_RUNNING_PATH, json={"model_id": "m1", "name": "x", "marked": True}, headers=headers)
    assert answer.status_code == 403 and not marks_file.exists()


def test_api_refuses_form_post_from_page(server, marks_file):
    answer = httpx.post(server + NOT_RUNNING_PATH, data={"model_id": "m1", "name": "x", "marked": "true"})
    assert answer.status_code == 403 and not marks_file.exists()


@pytest.mark.parametrize("body", ["nije json", '{"model_id":"","name":"x","marked":true}', '{"model_id":"m","name":"x","marked":"da"}', "[]"])
def test_api_refuses_bad_body(server, marks_file, body):
    answer = httpx.post(server + NOT_RUNNING_PATH, content=body, headers={"Content-Type": "application/json"})
    assert answer.status_code == 400 and not marks_file.exists()
