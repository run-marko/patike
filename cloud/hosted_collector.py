"""Private hosted transport for the two direct retailer adapters."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlsplit

import httpx

from common.http import PoliteClient, USER_AGENT
from cloud.direct_collector import HOSTS, download, crypt

ORIGIN = 'https://marko-direct-collector.marko-aksic85.chatgpt.site'
ROOT = Path(__file__).resolve().parent.parent


class HostedTransport(httpx.BaseTransport):
    def __init__(self, token: str, gateway: httpx.Client | None = None):
        if not token or '\n' in token or '\r' in token:
            raise ValueError('Invalid service credential')
        self.token = token
        self.gateway = gateway or httpx.Client(timeout=40, follow_redirects=False)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        if request.method != 'GET' or url.scheme != 'https' or url.hostname not in HOSTS.values() or url.username or url.password:
            raise httpx.RequestError('Collector source is not allowed', request=request)
        response = self.gateway.post(ORIGIN + '/fetch', json={'url': str(request.url)},
                                     headers={'OAI-Sites-Authorization': 'Bearer ' + self.token})
        return httpx.Response(response.status_code, content=response.content,
                              headers={'content-type': response.headers.get('content-type', 'text/plain')}, request=request)

    def close(self):
        self.gateway.close()


def hosted_client(base_url: str, scan: dict) -> PoliteClient:
    token = os.environ['HOSTED_COLLECTOR_TOKEN']
    client = httpx.Client(transport=HostedTransport(token), headers={'User-Agent': USER_AGENT, 'Accept-Language': 'sr,en;q=0.5'},
                         timeout=40, follow_redirects=True)
    return PoliteClient(base_url, delay_s=scan['default_delay_s'], timeout_s=scan['request_timeout_s'],
                        max_retries=scan['max_retries'], client=client)


def configure() -> None:
    config = ROOT / 'cloud' / 'collector-service.enc.json'
    if not config.exists():
        print('Hosted collector credentials are not activated.')
        return
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        encrypted = download('collector-service-key.enc', directory)
        private = directory / 'private.pem'
        crypt('dec', encrypted, private)
        proc = subprocess.run(['node', str(ROOT / 'cloud' / 'open_service_config.mjs'), str(private), str(config)],
                              capture_output=True, text=True, timeout=60)
        # Only masked workflow control output is printed by the helper.
        if proc.returncode:
            raise RuntimeError('Unable to activate the hosted collector credential')
        print(proc.stdout, end='')
    print('Private hosted collector enabled for Djak and Planeta.')


if __name__ == '__main__':
    configure()
