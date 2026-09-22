from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import time

import aiohttp
from aiohttp import web
import psutil
import pytest
import tomlkit

from codexio.upstream_client import identity, desktop_target
from codexio.upstream_config import OfficialRoute, ROUTE_ID, ROUTE_HEADER, UpstreamError
from codexio.upstream_proxy import Relay
from codexio.upstream_service import UpstreamService
from codexio.upstream_store import ResponseObserver, UpstreamStore, enrich_rows


@pytest.fixture
def route(tmp_path):
    config = tmp_path / 'codex/config.toml'
    config.parent.mkdir()
    config.write_text('# 用户注释\nmodel="gpt-6-astra"\nmodel_provider="openai"\n', encoding='utf-8')
    return OfficialRoute(config, tmp_path / 'data/upstream')


def test_route_roundtrip_preserves_original_and_does_not_touch_auth(route):
    original = route.config.read_bytes()
    auth = route.config.with_name('auth.json')
    auth.write_bytes(b'unchanged-oauth-sentinel')
    route.install('http://127.0.0.1:6543/v1', 'private-route')
    config = tomlkit.parse(route.config.read_text(encoding='utf-8'))
    table = config['model_providers'][ROUTE_ID]
    assert config['model_provider'] == ROUTE_ID
    assert table['requires_openai_auth'] is True and table['supports_websockets'] is False
    assert table['name'] == 'OpenAI' and 'api_key' not in table
    # Simulate the official client rotating its native credential independently.
    auth.write_bytes(b'new-oauth-sentinel')
    route.restore()
    assert route.config.read_bytes() == original
    assert auth.read_bytes() == b'new-oauth-sentinel'
    assert not route.journal.exists()


def test_restore_preserves_concurrent_user_edits(route):
    route.install('http://127.0.0.1:6543/v1', 'token')
    text = route.config.read_text(encoding='utf-8').replace('gpt-6-astra', 'gpt-5.6-luna')
    route.config.write_text(text + '\n[features]\nmy_feature=true # keep\n', encoding='utf-8')
    route.restore()
    text = route.config.read_text(encoding='utf-8')
    config = tomlkit.parse(text)
    assert config['model_provider'] == 'openai'
    assert config['model'] == 'gpt-5.6-luna' and config['features']['my_feature']
    assert '# keep' in text and '# 用户注释' in text and ROUTE_ID not in text


def test_restore_never_overwrites_another_managers_route(route):
    route.install('http://127.0.0.1:6543/v1', 'token')
    config = tomlkit.parse(route.config.read_text(encoding='utf-8'))
    config['model_provider'] = 'other'
    config['model_providers']['other'] = {'base_url': 'https://example.invalid'}
    route.config.write_text(tomlkit.dumps(config), encoding='utf-8')
    route.restore()
    result = tomlkit.parse(route.config.read_text(encoding='utf-8'))
    assert result['model_provider'] == 'other' and 'other' in result['model_providers']
    assert ROUTE_ID not in result['model_providers']


def test_conflicting_owned_route_keeps_journal_for_recovery(route):
    route.install('http://127.0.0.1:6543/v1', 'token')
    route.config.write_text(route.config.read_text(encoding='utf-8').replace(':6543/', ':6544/'), encoding='utf-8')
    with pytest.raises(UpstreamError):
        route.restore()
    assert route.journal.is_file()


@pytest.mark.parametrize('text', ['model_provider="third-party"', 'forced_login_method="api"',
                                'profile="a"\n[profiles.a]\nmodel_provider="third-party"',
                                'openai_base_url="https://example.invalid"', 'invalid = ['])
def test_preflight_refuses_custom_routes_without_modifications(route, text):
    route.config.write_text(text, encoding='utf-8')
    with pytest.raises(UpstreamError):
        route.install('http://127.0.0.1:6543/v1', 'token')
    assert route.config.read_text(encoding='utf-8') == text and not route.journal.exists()


def test_unset_and_missing_config_are_restored(tmp_path):
    route = OfficialRoute(tmp_path / 'config.toml', tmp_path / 'state')
    route.install('http://127.0.0.1:6543/v1', 'token')
    route.restore()
    assert not route.config.exists()
    route.config.write_text('# original\nmodel="gpt-6-astra"\n', encoding='utf-8')
    original = route.config.read_bytes()
    route.install('http://127.0.0.1:6543/v1', 'token')
    route.restore()
    assert route.config.read_bytes() == original


def sse(ident='resp_1', model='gpt-5.6-luna', kind='response.completed'):
    return ('data: ' + json.dumps({'type': kind, 'response': {'id': ident, 'model': model, 'output': 'private user text'}}) + '\r\n\r\n').encode()


@pytest.mark.parametrize('chunk_size', [1, 2, 3, 7, 64, 4096])
def test_stream_observer_handles_arbitrary_boundaries(chunk_size):
    events = []
    observer = ResponseObserver(lambda *value: events.append(value))
    payload = b': comment\r\n\r\n' + sse(kind='response.created') + sse() + b'data: [DONE]\r\n\r\n'
    for offset in range(0, len(payload), chunk_size):
        observer.feed(payload[offset:offset + chunk_size])
    assert events == [('resp_1', 'gpt-5.6-luna', 'response.created'), ('resp_1', 'gpt-5.6-luna', 'response.completed')]


def test_oversized_and_malformed_events_do_not_block_following_events():
    events = []
    observer = ResponseObserver(lambda *value: events.append(value))
    observer.MAX_EVENT = 300
    for _ in range(100):
        observer.feed(b'x' * 1000)
        assert len(observer.buffer) <= 300
    observer.feed(b'\n\ndata: invalid-json\n\n' + sse())
    assert events == [('resp_1', 'gpt-5.6-luna', 'response.completed')]


def test_metadata_precedence_and_no_invented_response_model(tmp_path):
    store = UpstreamStore(tmp_path / 'upstream.sqlite')
    store.record('resp_1', 'created-model', 'response.created')
    store.record('resp_1', 'actual-model')
    store.record('resp_1', 'late-created-model', 'response.created')
    store.record('resp_2', None)
    assert store.lookup(['resp_1', 'resp_2']) == {'resp_1': 'actual-model'}
    assert store.revision() == 2
    observer = ResponseObserver(store.record, sse=False)
    observer.feed(b'{"id":"resp_3","model":"json-model","output":"private response"}')
    observer.finish()
    assert store.lookup(['resp_3']) == {'resp_3': 'json-model'}
    assert b'private response' not in store.path.read_bytes()


def test_enrichment_keeps_requested_model_and_billing(tmp_path):
    db = sqlite3.connect(':memory:')
    db.executescript('CREATE TABLE usage_request_members(request_id,record_id); CREATE TABLE usage_priced_calls(id,data);')
    for i in range(3):
        db.execute('INSERT INTO usage_request_members VALUES(?,?)', ('request1', str(i)))
        db.execute('INSERT INTO usage_priced_calls VALUES(?,?)', (str(i), json.dumps({'response_id': 'resp_' + str(i)})))
    store = UpstreamStore(tmp_path / 'upstream.sqlite')
    store.record('resp_0', 'upstream-a')
    store.record('resp_1', 'upstream-b')
    rows = [{'id': 'request1', 'model': 'requested', 'call_count': 3, 'cost_usd': 1.25, 'total_tokens': 125}]
    enriched = enrich_rows(db, rows, store, grouped=True)[0]
    assert enriched['upstream_models'] == ['upstream-a', 'upstream-b']
    assert enriched['upstream_detected_calls'] == 2 and enriched['upstream_total_calls'] == 3
    assert enriched['model'] == 'requested' and enriched['cost_usd'] == 1.25 and enriched['total_tokens'] == 125
    assert enrich_rows(db, [{'id': 'old', 'response_id': None}], store) == [{'id': 'old', 'response_id': None}]


async def _relay_test(tmp_path, action):
    received = []
    async def upstream(request):
        received.append((request.path, dict(request.headers), await request.read()))
        if request.path.endswith('redirect'):
            return web.Response(status=302, headers={'Location': 'https://example.invalid/steal'})
        if request.path.endswith('error'):
            return web.Response(status=429, body=b'original error')
        if request.path.endswith('json'):
            return web.json_response({'id': 'resp_json', 'model': 'json-model'})
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        await response.prepare(request)
        data = sse(kind='response.created') + sse()
        for offset in range(0, len(data), 7):
            await response.write(data[offset:offset + 7])
            await asyncio.sleep(0)
        return response
    upstream_app = web.Application()
    upstream_app.router.add_route('*', '/official/{rest:.*}', upstream)
    runner = web.AppRunner(upstream_app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    config = tmp_path / 'config.toml'
    config.write_text('# original\n', encoding='utf-8')
    relay = Relay(tmp_path / 'upstream', dict(config=str(config), owner=identity(psutil.Process()),
                  route_token='route-secret', control_token='control-secret'),
                  origin=f'http://127.0.0.1:{port}/official', dependents=lambda: [])
    address = await relay.start()
    try:
        async with aiohttp.ClientSession() as client:
            control = {'Authorization': 'Bearer control-secret'}
            headers = {'Authorization': 'Bearer oauth-sentinel', ROUTE_HEADER: 'route-secret', 'ChatGPT-Account-ID': 'account-sentinel'}
            async with client.post(address + '/_codexio/activate', json={}, headers=control) as result:
                assert (await result.json())['capture']
            await action(client, address, relay, received, headers, control)
    finally:
        relay.detach()
        await relay.close()
        await runner.cleanup()


def test_http_sse_auth_passthrough_no_redirect_and_clean_deactivation(tmp_path):
    async def action(client, address, relay, received, headers, control):
        async with client.post(address + '/v1/responses', headers=headers, data=b'private user message') as response:
            assert response.status == 200
            assert await response.read() == sse(kind='response.created') + sse()
        await relay.queue.join()
        assert relay.store.lookup(['resp_1']) == {'resp_1': 'gpt-5.6-luna'}
        path, sent, body = received[0]
        assert path == '/official/responses' and body == b'private user message'
        assert sent['Authorization'] == 'Bearer oauth-sentinel' and sent['ChatGPT-Account-ID'] == 'account-sentinel'
        assert ROUTE_HEADER not in sent
        import gzip
        compressed = gzip.compress(b'compressed private message')
        async with client.post(address + '/v1/responses', headers=dict(headers, **{'Content-Encoding': 'gzip'}), data=compressed) as response:
            assert response.status == 200
            await response.read()
        assert received[-1][2] == b'compressed private message'
        for target, status, body in [('error', 429, b'original error'), ('redirect', 302, b'')]:
            async with client.post(address + '/v1/' + target, headers=headers, allow_redirects=False) as response:
                assert response.status == status and await response.read() == body
        async with client.post(address + '/v1/responses', headers={'Authorization': 'Bearer oauth-sentinel'}) as response:
            assert response.status == 403
        async with client.post(address + '/_codexio/deactivate', headers=control) as response:
            assert (await response.json())['capture'] is False
        assert relay.route.config.read_text(encoding='utf-8') == '# original\n'
        async with client.post(address + '/v1/json', headers=headers) as response:
            assert response.status == 200
        await relay.queue.join()
        assert relay.store.lookup(['resp_json']) == {}
        assert b'private user message' not in relay.store.path.read_bytes()
        assert b'oauth-sentinel' not in relay.store.path.read_bytes()
    asyncio.run(_relay_test(tmp_path, action))


def test_guardian_restores_after_owner_loss_without_restarting_client(tmp_path):
    async def action(client, address, relay, received, headers, control):
        relay.guards = [identity(psutil.Process())]  # stand-in old desktop process
        relay.owner = None
        await asyncio.sleep(1.2)
        assert not relay.capture and not relay.stopping.is_set()
        assert not relay.route.journal.exists()
        async with client.post(address + '/v1/json', headers=headers) as response:
            assert response.status == 200
    asyncio.run(_relay_test(tmp_path, action))


def test_self_update_claim_keeps_capture_without_another_restart(tmp_path):
    async def action(client, address, relay, received, headers, control):
        async with client.post(address + '/_codexio/handoff', headers=control) as response:
            assert response.status == 200
        relay.owner = None
        await asyncio.sleep(1.2)
        assert relay.capture
        async with client.post(address + '/_codexio/claim', headers=control,
                               json={'owner': identity(psutil.Process()), 'update': True}) as response:
            assert (await response.json())['capture']
        assert relay.route.journal.exists()
    asyncio.run(_relay_test(tmp_path, action))


def test_headless_helper_process_start_restore_and_exit(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTHONPATH', str(Path(__file__).resolve().parents[1] / 'src'))
    config = tmp_path / 'codex/config.toml'
    config.parent.mkdir()
    config.write_text('# original\n', encoding='utf-8')
    restarts = []
    service = UpstreamService(tmp_path / 'data', config_path=config,
                              restart=lambda: restarts.append(True), verify_login=lambda: None)
    try:
        service.enable()
        assert service.active and service.healthy()
        assert tomlkit.parse(config.read_text(encoding='utf-8'))['model_provider'] == ROUTE_ID
        service.disable()
        assert not service.active and restarts == [True, True]
        assert config.read_text(encoding='utf-8') == '# original\n'
    finally:
        service.abandon()
        # Real unrelated Codex processes may keep pure forwarding alive. This
        # isolated helper can be terminated once its temp config is restored.
        if service.process:
            service.process.terminate()
            service.process.wait(timeout=10)


def test_desktop_discovery_excludes_cli_renderers_and_unrelated_apps(tmp_path):
    exe = tmp_path / 'Codex.exe'
    exe.write_bytes(b'exe')
    assert desktop_target(exe, platform='win32') is None
    resources = tmp_path / 'resources'
    resources.mkdir()
    (resources / 'app.asar').touch()
    assert desktop_target(exe, platform='win32')['exe'] == str(exe)
    assert desktop_target(exe, ['--type=renderer'], platform='win32') is None
    mac = tmp_path / 'ChatGPT.app/Contents/MacOS/ChatGPT'
    mac.parent.mkdir(parents=True)
    with (mac.parent.parent / 'Info.plist').open('wb') as stream:
        plistlib.dump({'CFBundleIdentifier': 'com.openai.codex'}, stream)
    assert desktop_target(mac, platform='darwin')['bundle'].endswith('ChatGPT.app')
    assert desktop_target(mac.parent.parent / 'Resources/codex', platform='darwin') is None
