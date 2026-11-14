import httpx
import pytest
from cloud.hosted_collector import HostedTransport, ORIGIN
from common.config import default_config
from retailers.registry import build_adapters
from cloud.direct_collector import CollectedAdapter


def test_original_url_body_and_source_status_are_preserved():
    seen = []
    def gateway(request):
        seen.append(request)
        return httpx.Response(200, text='<html>Direct product</html>', headers={'content-type':'text/html'})
    transport = HostedTransport('synthetic-test-credential', gateway=httpx.Client(transport=httpx.MockTransport(gateway)))
    with httpx.Client(transport=transport) as client:
        response = client.get('https://www.djaksport.com/patike-nike')
    assert str(response.url) == 'https://www.djaksport.com/patike-nike'
    assert response.text == '<html>Direct product</html>'
    assert str(seen[0].url) == ORIGIN + '/fetch'
    assert seen[0].headers['OAI-Sites-Authorization'] == 'Bearer synthetic-test-credential'
    assert b'www.djaksport.com' in seen[0].content


def test_source_block_is_not_turned_into_a_success():
    transport = HostedTransport('synthetic-test-credential', gateway=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(403,text='Source blocked'))))
    with httpx.Client(transport=transport) as client:
        assert client.get('https://planetasport.rs/patike.html').status_code == 403


@pytest.mark.parametrize('url', ['https://ananas.rs/proizvod/patike/1','https://attacker.test/','http://www.djaksport.com/patike'])
def test_other_sources_never_receive_the_credential(url):
    transport = HostedTransport('synthetic-test-credential', gateway=httpx.Client(transport=httpx.MockTransport(
        lambda request: pytest.fail('A request must not be sent'))))
    with httpx.Client(transport=transport) as client, pytest.raises(httpx.RequestError):
        client.get(url)


def test_hosted_collector_takes_precedence_over_local_collection(monkeypatch):
    monkeypatch.setenv('HOSTED_COLLECTOR_TOKEN','synthetic-test-credential')
    monkeypatch.setenv('DIRECT_COLLECTOR_RUN_ID','123')
    adapters = build_adapters(default_config())
    assert not any(isinstance(a, CollectedAdapter) for a in adapters)
    for a in adapters:
        transport = a.client._client._transport
        assert isinstance(transport, HostedTransport) == (a.key in {'djaksport','planetasport'})


def test_hosted_scan_never_requests_a_local_collector(monkeypatch):
    from cloud.direct_collector import request
    monkeypatch.setenv('HOSTED_COLLECTOR_TOKEN','synthetic-test-credential')
    monkeypatch.delenv('GITHUB_RUN_ID',raising=False)
    request()
