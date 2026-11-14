import os
import shutil
from pathlib import Path

import pytest

from cloud import direct_collector as bridge
from cloud.start_direct_collector import protect


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI is only available on Windows')
def test_windows_credentials_are_encrypted_and_round_trip():
    original = os.urandom(64)
    encrypted = protect(original)
    assert original not in encrypted
    assert protect(encrypted, decrypt=True) == original


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is required by the existing encryption')
def test_encrypted_result_round_trip_and_wrong_password(tmp_path, monkeypatch):
    source = tmp_path / 'plain.json'
    source.write_text('{"sku":"JH6206","price":13799}', encoding='utf-8')
    encrypted, decoded = tmp_path / 'results.enc', tmp_path / 'decoded.json'
    monkeypatch.setenv('PAGE_PASSWORD', os.urandom(32).hex())
    bridge.crypt('enc', source, encrypted)
    assert b'JH6206' not in encrypted.read_bytes()
    bridge.crypt('dec', encrypted, decoded)
    assert decoded.read_bytes() == source.read_bytes()
    monkeypatch.setenv('PAGE_PASSWORD', os.urandom(32).hex())
    with pytest.raises(RuntimeError):
        bridge.crypt('dec', encrypted, tmp_path / 'wrong.json')
    assert not (tmp_path / 'wrong.json').exists()
