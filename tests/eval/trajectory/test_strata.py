from http.client import HTTPConnection
import json
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import httpx
import pytest

from tools.run_agent_eval import parser
from tests.eval.trajectory.safety import validate
from tests.eval.trajectory.test_safety import args


def test_separate_cli_does_not_accept_auth_options():
    required = ['--native-cli', 'codex.exe', '--scenario', 'normal', '--output-root', 'new',
                '--fixtures-manifest', 'manifest.json', '--product-render', 'E0/render.png', '--port', '3232']
    options = parser().parse_args(['live-strata', *required])
    assert options.mode == 'live'
    assert options.strata and options.autonomy == 'standard'
    assert not options.ack_paid_model_approval and options.auth_home_source is None
    for injected in [['--auth-home-source', 'auth'], ['--model', 'other'], ['--llm-base-url', 'https://other'],
                     ['--autonomy', 'autonomous'], ['--ack-paid-model-approval']]:
        with pytest.raises(SystemExit):
            parser().parse_args(['live-strata', *required, *injected])


@pytest.mark.parametrize('changed', [None, 'model', 'llm_base_url', 'auth_home_source', 'ack_owned_auth_copy',
                                     'ack_paid_model_approval', 'scenario', 'autonomy', 'host'])
def test_validate_strata_never_reads_auth_copy(tmp_path, changed):
    from tests.eval.trajectory.strata import MODEL, ENDPOINT

    options = args(tmp_path)
    source = options.auth_home_source
    source.rename(source.with_name('unused'))
    options.strata, options.host, options.autonomy, options.scenario = True, 'codex', 'standard', 'normal'
    options.model, options.llm_base_url = MODEL, ENDPOINT
    options.auth_home_source = None
    options.ack_owned_auth_copy = options.ack_paid_model_approval = False
    if changed:
        setattr(options, changed, {'model': 'other', 'llm_base_url': ENDPOINT + '/',
                                  'auth_home_source': source, 'ack_owned_auth_copy': True,
                                  'ack_paid_model_approval': True, 'scenario': 'transient',
                                  'autonomy': 'autonomous', 'host': 'pi'}[changed])
        with pytest.raises(ValueError, match='Strata'):
            validate(options)
    else:
        output, auth, _ = validate(options)
        assert auth is None and not output.exists()


def post_to_strata_relay(relay, *, suffix='/responses', model=None, headers=None):
    from tests.eval.trajectory.strata import MODEL

    opener = build_opener(ProxyHandler({}))
    return opener.open(Request(relay.base_url + suffix, json.dumps({'model': model or MODEL, 'input': 'synthetic'}).encode(),
                               headers={'Content-Type': 'application/json'} | (headers or {})), timeout=5)


def test_relay_streams_only_fixed_unsigned_request_and_respects_limit(tmp_path):
    from tests.eval.trajectory.strata import ENDPOINT, MODEL, ResponsesRelay

    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, content=b'event: example\ndata: {}\n\n')

    with ResponsesRelay(tmp_path, limit=1, timeout=5, transport=httpx.MockTransport(upstream)) as relay:
        for kwargs, code in [({'suffix': '/responses?other=1'}, 404), ({'model': 'other'}, 400),
                             ({'headers': {'Authorization': 'Bearer not-a-real-secret'}}, 400),
                             ({'headers': {'Cookie': 'synthetic=1'}}, 400)]:
            with pytest.raises(HTTPError) as exc:
                post_to_strata_relay(relay, **kwargs)
            assert exc.value.code == code
        opener = build_opener(ProxyHandler({}))
        for method, target in [('GET', '/models'), ('CONNECT', '/responses')]:
            with pytest.raises(HTTPError):
                opener.open(Request(relay.base_url + target, method=method), timeout=5)
        connection = HTTPConnection('127.0.0.1', relay.server.server_port, timeout=5)
        try:
            connection.request('POST', 'http://other.invalid/v1/responses')
            assert connection.getresponse().status == 404
        finally:
            connection.close()
        with post_to_strata_relay(relay, headers={'X-Untrusted': 'not forwarded'}) as response:
            assert response.read() == b'event: example\ndata: {}\n\n'
        with pytest.raises(HTTPError) as exc:
            post_to_strata_relay(relay)
        assert exc.value.code == 503
    request, = requests
    assert request.method == 'POST' and str(request.url) == ENDPOINT + '/responses'
    assert json.loads(request.content)['model'] == MODEL
    assert all(key not in request.headers for key in ['authorization', 'cookie', 'x-untrusted'])


@pytest.mark.parametrize('status', [302, 401, 403])
def test_relay_does_not_retry_auth_or_follow_redirects(tmp_path, status):
    from tests.eval.trajectory.strata import ResponsesRelay

    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(status, headers={'Location': 'https://other.invalid/credential', 'Set-Cookie': 'synthetic=1'},
                              content=b'upstream rejected')

    with ResponsesRelay(tmp_path, limit=3, timeout=5, transport=httpx.MockTransport(upstream)) as relay:
        for code in [status if status in {401, 403} else 502, 503]:
            with pytest.raises(HTTPError) as exc:
                post_to_strata_relay(relay)
            assert exc.value.code == code
            assert 'set-cookie' not in exc.value.headers
        reason = 'rejected unauthenticated Responses' if status in {401, 403} else 'Responses redirected'
        assert f'{reason} (HTTP {status})' in relay.failure
    assert len(requests) == 1


def test_altered_fixture_manifest_does_not_reach_setup(tmp_path):
    from tests.eval.trajectory.strata import PRODUCT_RENDER, require_strata_fixture

    manifest = tmp_path / 'manifest.json'
    manifest.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='approved #404'):
        require_strata_fixture(manifest, PRODUCT_RENDER)
