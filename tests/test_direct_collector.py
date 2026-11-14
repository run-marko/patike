import copy
import dataclasses
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from cloud import direct_collector as bridge
from common.config import default_config
from database.models import Listing, PriceObservation
from retailers.base import RetailerResult
from retailers.djaksport import DjakSportAdapter
from retailers.planetasport import PlanetaSportAdapter
from retailers.registry import build_adapters
from scanner import run_scan


@pytest.fixture
def snapshot():
    item = DjakSportAdapter().parse_page(
        (Path(__file__).parent / 'fixtures/djak_evo_sl.html').read_text(),
        'https://www.djaksport.com/adidas-patike-adizero-evo-sl-m-za-muskarce-jh6206')[0]
    item.running = True
    djak = RetailerResult('djaksport', 'VERIFIED', listings=[item], pages_ok=1, candidates=1)
    planeta = copy.deepcopy(item)
    planeta.retailer = 'planetasport'
    planeta.url = 'https://planetasport.rs/adidas-patike-evo-sl-jh6206.html'
    result = RetailerResult('planetasport', 'PARTIAL', message='Only a test page', listings=[planeta],
                            pages_ok=1, pages_failed=1, candidates=2)
    return {'version': 1, 'repository': bridge.REPOSITORY, 'run_id': '123', 'created_at': bridge.now(),
            'results': {'djaksport': dataclasses.asdict(djak), 'planetasport': dataclasses.asdict(result)}}


def test_direct_results_preserve_prices_sizes_and_incomplete_status(snapshot):
    results = bridge.decode_results(snapshot, '123')
    assert results['djaksport'].listings[0].current_price == 13799
    assert len(results['djaksport'].listings[0].sizes) == 8
    assert results['planetasport'].status == 'PARTIAL'
    assert results['planetasport'].pages_failed == 1


@pytest.mark.parametrize('field,value', [('run_id', '124'), ('repository', 'someone/else'),
                                         ('version', 2), ('created_at', 0)])
def test_wrong_scan_or_stale_response_is_rejected(snapshot, field, value):
    snapshot[field] = value
    with pytest.raises(ValueError):
        bridge.decode_results(snapshot, '123')


@pytest.mark.parametrize('url', ['https://ananas.rs/proizvod/shoe/1',
                                  'https://www.djaksport.com.attacker.test/shoe',
                                  'https://user:secret@www.djaksport.com/shoe',
                                  'http://www.djaksport.com/shoe'])
def test_marketplace_and_wrong_origin_are_never_imported(snapshot, url):
    snapshot['results']['djaksport']['listings'][0]['url'] = url
    with pytest.raises(ValueError):
        bridge.decode_results(snapshot, '123')


def test_marketplace_seller_is_rejected_even_with_direct_url(snapshot):
    snapshot['results']['djaksport']['listings'][0]['seller'] = 'Đak / Ananas'
    with pytest.raises(ValueError):
        bridge.decode_results(snapshot, '123')


@pytest.mark.parametrize('price', [-1, 0, float('nan'), float('inf'), True, '13799'])
def test_bad_price_is_not_used_for_ranking(snapshot, price):
    snapshot['results']['djaksport']['listings'][0]['current_price'] = price
    with pytest.raises(ValueError):
        bridge.decode_results(snapshot, '123')


def test_unverified_source_cannot_claim_listings(snapshot):
    snapshot['results']['djaksport']['status'] = 'NOT_VERIFIED'
    with pytest.raises(ValueError):
        bridge.decode_results(snapshot, '123')


def test_response_download_is_shared_between_both_threads(snapshot, monkeypatch):
    downloads = []
    monkeypatch.setattr(bridge, 'assets', lambda: {'direct-response-123.enc': {}})
    def download(name, folder):
        downloads.append(name)
        path = folder / name
        path.write_bytes(b'encrypted')
        return path
    monkeypatch.setattr(bridge, 'download', download)
    monkeypatch.setattr(bridge, 'crypt', lambda mode, source, target: target.write_text(json.dumps(snapshot)))
    source = bridge.ResponseSource('123', wait_seconds=0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: source.load(), range(2)))
    assert values[0] is values[1]
    assert downloads == ['direct-response-123.enc']


def test_missing_collector_produces_failure_without_fabricated_prices(monkeypatch):
    monkeypatch.setattr(bridge, 'assets', lambda: {})
    results = bridge.ResponseSource('123', wait_seconds=0).load()
    assert all(r.status == 'NOT_VERIFIED' and not r.listings for r in results.values())


def test_corrupt_ciphertext_does_not_enter_database(monkeypatch):
    monkeypatch.setattr(bridge, 'assets', lambda: {'direct-response-123.enc': {}})
    monkeypatch.setattr(bridge, 'download', lambda name, folder: folder / name)
    def fail(*args):
        raise RuntimeError('Authentication failed')
    monkeypatch.setattr(bridge, 'crypt', fail)
    results = bridge.ResponseSource('123', wait_seconds=0).load()
    assert all(r.status == 'NOT_VERIFIED' and not r.listings for r in results.values())


def test_import_joins_normal_scan_database_and_history(snapshot, factory):
    source = bridge.ResponseSource('123')
    source.results = bridge.decode_results(snapshot, '123')
    adapters = [bridge.CollectedAdapter(DjakSportAdapter(), source),
                bridge.CollectedAdapter(PlanetaSportAdapter(), source)]
    summary = run_scan(factory, default_config(), adapters=adapters)
    assert summary.saved == 2
    with factory() as session:
        rows = list(session.scalars(select(Listing)))
        assert {row.retailer for row in rows} == {'djaksport', 'planetasport'}
        observations = list(session.scalars(select(PriceObservation).where(
            PriceObservation.price_type == 'CURRENT_PUBLIC')))
        assert len(observations) == 2
        assert {obs.scan_id for obs in observations} == {summary.scan_id}
        assert all(len(obs.sizes) == 8 and obs.price == 13799 for obs in observations)


def test_registry_only_replaces_the_two_direct_retailers(monkeypatch):
    monkeypatch.delenv('DIRECT_COLLECTOR_RUN_ID', raising=False)
    normal = build_adapters(default_config())
    assert not any(isinstance(a, bridge.CollectedAdapter) for a in normal)
    monkeypatch.setenv('DIRECT_COLLECTOR_RUN_ID', '123')
    connected = build_adapters(default_config())
    assert {a.key for a in connected if isinstance(a, bridge.CollectedAdapter)} == set(bridge.HOSTS)
    assert [type(a) for a in connected if a.key not in bridge.HOSTS] == [
        type(a) for a in normal if a.key not in bridge.HOSTS]


def test_offline_heartbeat_never_delays_existing_scan(tmp_path, monkeypatch):
    envfile = tmp_path / 'env'
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('GITHUB_ENV', str(envfile))
    def unavailable(*args):
        raise RuntimeError('No collector')
    monkeypatch.setattr(bridge, 'download', unavailable)
    bridge.request()
    assert not envfile.exists()


def test_fresh_heartbeat_creates_unique_request_and_only_safe_environment(tmp_path, monkeypatch):
    envfile = tmp_path / 'env'
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('GITHUB_ENV', str(envfile))
    def download(name, folder):
        path = folder / name
        path.write_text(json.dumps({'version': 1, 'repository': bridge.REPOSITORY, 'updated_at': bridge.now()}))
        return path
    monkeypatch.setattr(bridge, 'download', download)
    uploaded = []
    monkeypatch.setattr(bridge, 'upload', lambda path: uploaded.append((path.name, json.loads(path.read_text()))))
    bridge.request()
    assert uploaded[0][0] == 'direct-request-123.json'
    assert bridge.validate_request(uploaded[0][1], '123') == '123'
    assert envfile.read_text() == 'DIRECT_COLLECTOR_RUN_ID=123\n'


@pytest.mark.parametrize('identity', ['../123', '12\nTOKEN=secret', '0', '-1', 'a123'])
def test_run_identity_cannot_be_a_path_or_environment_injection(identity):
    with pytest.raises(ValueError):
        bridge.run_id(identity)


def test_heartbeat_expiry_is_bounded():
    with pytest.raises(ValueError):
        bridge.fresh_timestamp(100, age=180, clock=1000)
    with pytest.raises(ValueError):
        bridge.fresh_timestamp(1061, age=180, clock=1000)


def test_scheduled_workflow_keeps_existing_scan_and_no_cooldown():
    import yaml
    root = Path(__file__).resolve().parent.parent
    raw = (root / '.github/workflows/dnevni-sken.yml').read_text()
    workflow = yaml.safe_load(raw)
    assert 'MANUAL_GAP_HOURS' not in raw
    steps = workflow['jobs']['sken']['steps']
    request = next(i for i, s in enumerate(steps) if s.get('run') == 'python -m cloud.direct_collector request')
    scan = next(i for i, s in enumerate(steps) if 'python main.py scan --top 20' in s.get('run', ''))
    assert request < scan
    assert workflow['concurrency'] == {'group': 'dnevni-sken', 'cancel-in-progress': False}
    assert 'cron: "0 5 * * *"' in raw
