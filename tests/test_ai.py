import base64
import copy
import json
import threading
import time
from pathlib import Path

import pytest

import ai_models
import ai_tasks
import ai_tools
import storage


CONFIG = {'base_url': 'http://localhost:1234/v1', 'model': 'custom-model', 'api_key': 'test-secret',
          'stream': True, 'vision': False, 'timeout': 10, 'max_rounds': 5}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    monkeypatch.setattr(ai_tasks, 'configuration', lambda: CONFIG.copy())


@pytest.fixture
def kit(tmp_path):
    return ai_tools.ToolKit(tmp_path)


def task(**changes):
    return {'id': 'assign-1', 'title': '作业', 'course': '课程',
            'url': 'https://selearning.nju.edu.cn/mod/assign/view.php?id=1', **changes}


def tool_call(name, args, id='call-1'):
    return {'id': id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}


def wait_job(manager, id='assign-1'):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with manager.lock:
            if id not in manager.workers:
                return manager.get_job(id)
        time.sleep(.005)
    raise AssertionError('任务没有结束')


def test_config_encrypted_and_validated(tmp_path):
    ai_models.save_configuration(CONFIG)
    assert ai_models.configuration() == ai_models.DEFAULTS | CONFIG
    assert b'test-secret' not in (tmp_path / 'ai-model.dpapi').read_bytes()
    for bad in ({'base_url': 'file:///etc'}, {'base_url': 'https://key:secret@example.com'},
                {'model': ''}, {'max_rounds': 0}, {'timeout': 2}):
        with pytest.raises(ValueError):
            ai_models.validate_config(CONFIG | bad)


@pytest.mark.parametrize('path', ['../cookies.dpapi', '/etc/passwd', 'C:/Windows/a', 'a:secret', 'NUL.txt'])
def test_workspace_escape_rejected(kit, path):
    result = kit.execute('write_file', json.dumps({'path': path, 'content': 'x'}))
    assert 'error' in result


def test_unknown_and_malformed_tools(kit):
    assert 'error' in kit.execute('run_shell', '{}')
    assert 'error' in kit.execute('read_file', '{')
    assert 'error' in kit.execute('read_file', '{"path":1}')
    assert 'error' in kit.execute('read_file', '{"path":"a","limit":-1}')
    assert 'error' in kit.execute('read_file', '{"path":"a","extra":true}')


def test_text_binary_and_pagination(kit):
    kit.write_file('outputs/中文.txt', '甲乙丙丁')
    first = kit.read_file('outputs/中文.txt', limit=2)
    assert first['content'] == '甲乙' and first['next_offset'] == 2
    assert kit.read_file('outputs/中文.txt', offset=2)['content'] == '丙丁'
    kit.write_file('outputs/中文.txt', '结束', append=True)
    assert kit.read_file('outputs/中文.txt')['content'].endswith('结束')
    raw = bytes(range(256))
    kit.write_file('outputs/data.unknown', base64.b64encode(raw).decode(), format='base64')
    result = kit.read_file('outputs/data.unknown', limit=32)
    assert base64.b64decode(result['content']) == raw[:32] and result['next_offset'] == 32
    assert kit.list_files('outputs')['total'] == 2


@pytest.mark.parametrize('format,content,expected', [
    ('docx', '你好\n这是作业答案。', '作业答案'),
    ('xlsx', '{"sheets":{"成绩":[["题目","答案"],[1,"=1+1"]]}}', '=1+1'),
    ('pptx', '{"slides":[{"title":"测试标题","text":"测试正文"}]}', '测试正文'),
    ('pdf', '中文作业\n正确答案是 42。', '42'),
])
def test_document_roundtrip(kit, format, content, expected):
    kit.write_file('outputs/result.' + format, content, format=format)
    assert expected in kit.read_file('outputs/result.' + format)['content']


def test_image_with_and_without_vision(kit):
    from PIL import Image
    Image.new('RGB', (40, 30), 'red').save(kit.root / 'sample.png')
    assert 'note' in kit.read_file('sample.png')
    kit.vision = True
    image = kit.read_file('sample.png')
    assert image['width'] == 40 and image['_image'].startswith('data:image/jpeg;base64,')


@pytest.mark.parametrize('name,raw', [('a.mp4', b''), ('a.MOV', b''), ('a.bin', b'\x00\x00\x00\x18ftypmp42')])
def test_video_rejected(kit, name, raw):
    with pytest.raises(ValueError, match='视频'):
        kit.write_file(name, base64.b64encode(raw).decode(), format='base64')
    (kit.root / name).write_bytes(raw)
    with pytest.raises(ValueError, match='视频'):
        kit.read_file(name, mode='base64')


def test_web_content_links_and_download(kit, monkeypatch):
    monkeypatch.setattr(kit, '_fetch', lambda url: ('https://example.com/a/', 'text/html',
        '<html><title>题目</title><script>secret script</script><p>证明</p><a href="../answer.pdf">附件</a></html>'.encode()))
    result = kit.read_webpage('https://example.com')
    assert result['title'] == '题目' and '证明' in result['content'] and 'secret script' not in result['content']
    assert result['links'][0]['url'] == 'https://example.com/answer.pdf'
    monkeypatch.setattr(kit, '_fetch', lambda url: (url, 'application/octet-stream', b'abc'))
    kit.download_file('https://example.com/a', 'source/a.bin')
    assert (kit.root / 'source/a.bin').read_bytes() == b'abc'


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://127.0.0.1/a', 'http://[::1]/a', 'http://user:secret@example.com'])
def test_private_links_rejected(url):
    with pytest.raises(ValueError):
        ai_tools.ToolKit._public_url(url)


class Response:
    def __init__(self, lines=None, result=None, status=200):
        self.lines = lines
        self.result = result
        self.status_code = status
        self.headers = {'Content-Type': 'text/event-stream' if lines is not None else 'application/json'}

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_lines(self, **kwargs): return iter(self.lines)
    def iter_content(self, *args): return iter([json.dumps(self.result).encode()])


def event(delta, finish=None):
    return ('data: ' + json.dumps({'choices': [{'delta': delta, 'finish_reason': finish}]})).encode()


def test_stream_reassembles_tool_arguments_and_uses_custom_endpoint(monkeypatch):
    seen = {}
    lines = [event({'content': '正在读取'}),
             event({'tool_calls': [{'index': 0, 'id': 'call-x', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":'}}]}),
             event({'tool_calls': [{'index': 0, 'function': {'arguments': '"题目.txt"}'}}]}),
             event({}, 'tool_calls'), b'data: [DONE]']
    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Response(lines=lines)
    monkeypatch.setattr(ai_models.requests, 'post', post)
    deltas = []
    answer = ai_models.ModelClient(CONFIG).complete([], ai_tools.SCHEMAS, threading.Event(), deltas.append)
    assert answer['tool_calls'][0]['function']['arguments'] == '{"path":"题目.txt"}'
    assert deltas == ['正在读取']
    assert seen['url'] == CONFIG['base_url'] + '/chat/completions'
    assert seen['json']['model'] == 'custom-model' and seen['allow_redirects'] is False
    assert seen['headers']['Authorization'] == 'Bearer test-secret'


def test_truncated_stream_never_returns_partial_tool(monkeypatch):
    monkeypatch.setattr(ai_models.requests, 'post', lambda *a, **k: Response(lines=[event({'tool_calls': [
        {'index': 0, 'id': 'x', 'function': {'name': 'write_file', 'arguments': '{}'}}]})]))
    with pytest.raises(RuntimeError, match='未完整结束'):
        ai_models.ModelClient(CONFIG).complete([], [], threading.Event())


def test_nonstream_and_http_failure(monkeypatch):
    monkeypatch.setattr(ai_models.requests, 'post', lambda *a, **k: Response(result={
        'choices': [{'message': {'content': '完成'}, 'finish_reason': 'stop'}]}))
    assert ai_models.ModelClient(CONFIG | {'stream': False}).complete([], [], threading.Event())['content'] == '完成'
    monkeypatch.setattr(ai_models.requests, 'post', lambda *a, **k: Response(status=401))
    with pytest.raises(RuntimeError, match='API Key 无效'):
        ai_models.ModelClient(CONFIG).complete([], [], threading.Event())


def test_full_tool_cycle_and_persistence(tmp_path, monkeypatch):
    calls = []
    def complete(self, messages, schemas, cancel, delta):
        calls.append(copy.deepcopy(messages))
        if len(calls) == 1:
            return {'role': 'assistant', 'content': '', 'tool_calls': [tool_call('write_file', {'path': 'outputs/答案.txt', 'content': '42'})]}
        delta('已完成')
        return {'role': 'assistant', 'content': '已完成'}
    monkeypatch.setattr(ai_tasks.ModelClient, 'complete', complete)
    manager = ai_tasks.TaskManager()
    manager.launch(task())
    job = wait_job(manager)
    assert job['state'] == 'completed'
    assert manager.workspace_files('assign-1')[0]['name'] == '答案.txt'
    assert calls[1][-1]['role'] == 'tool' and calls[1][-1]['tool_call_id'] == 'call-1'
    assert 'test-secret' not in json.dumps(calls)
    assert 'test-secret' not in (tmp_path / 'ai-jobs.json').read_text(encoding='utf-8')
    restored = ai_tasks.TaskManager()
    assert restored.jobs['assign-1']['messages'] == manager.jobs['assign-1']['messages']
    assert manager.launch(task())['task_id'] == 'assign-1'


def test_tool_error_is_sent_back_to_model(monkeypatch):
    calls = []
    def complete(self, messages, schemas, cancel, delta):
        calls.append(copy.deepcopy(messages))
        if len(calls) == 1:
            return {'role': 'assistant', 'content': '', 'tool_calls': [tool_call('read_file', {'path': '../secret'})]}
        assert 'error' in json.loads(messages[-1]['content'])
        return {'role': 'assistant', 'content': '无法读取'}
    monkeypatch.setattr(ai_tasks.ModelClient, 'complete', complete)
    manager = ai_tasks.TaskManager(); manager.launch(task())
    assert wait_job(manager)['state'] == 'completed'


def test_queue_and_independent_tasks(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def complete(self, messages, schemas, cancel, delta):
        prompt = messages[-1]['content']; calls.append(prompt)
        if not entered.is_set():
            entered.set(); assert release.wait(3)
        return {'role': 'assistant', 'content': '完成'}
    monkeypatch.setattr(ai_tasks.ModelClient, 'complete', complete)
    manager = ai_tasks.TaskManager(); first = manager.launch(task())
    assert entered.wait(2)
    manager.continue_job('assign-1', '补充测试')
    second = manager.launch(task(id='assign-2'))
    assert wait_job(manager, 'assign-2')['state'] == 'completed'
    assert first['workspace'] != second['workspace']
    release.set()
    assert wait_job(manager)['state'] == 'completed'
    assert calls[-1] == '补充测试'
    assert manager.get_job('assign-1')['pending_prompts'] == []


def test_stop_prevents_tool_execution_and_preserves_queue(monkeypatch):
    entered = threading.Event()
    def complete(self, messages, schemas, cancel, delta):
        entered.set(); assert cancel.wait(3)
        return {'role': 'assistant', 'content': '', 'tool_calls': [tool_call('write_file', {'path': 'outputs/bad.txt', 'content': 'bad'})]}
    monkeypatch.setattr(ai_tasks.ModelClient, 'complete', complete)
    manager = ai_tasks.TaskManager(); manager.launch(task())
    assert entered.wait(2)
    manager.continue_job('assign-1', '排队要求')
    manager.interrupt('assign-1')
    job = wait_job(manager)
    assert job['state'] == 'interrupted' and len(job['pending_prompts']) == 1
    assert not manager.workspace_files('assign-1')
    monkeypatch.setattr(ai_tasks.ModelClient, 'complete', lambda *args: {'role': 'assistant', 'content': '恢复完成'})
    manager.reconnect('assign-1')
    assert wait_job(manager)['state'] == 'completed'


def test_round_limit_keeps_valid_tool_history(monkeypatch):
    monkeypatch.setattr(ai_tasks.ModelClient, 'complete', lambda *args: {'role': 'assistant', 'content': '',
        'tool_calls': [tool_call('list_files', {})]})
    manager = ai_tasks.TaskManager(); manager.launch(task())
    job = wait_job(manager)
    assert job['state'] == 'error' and '上限' in job['message']
    messages = manager.jobs['assign-1']['messages']
    assert len([m for m in messages if m['role'] == 'tool']) == CONFIG['max_rounds']


def test_timeout_after_stop_stays_interrupted(monkeypatch):
    entered = threading.Event()
    def complete(self, messages, schemas, cancel, delta):
        entered.set(); assert cancel.wait(3)
        raise RuntimeError('网络请求超时')
    monkeypatch.setattr(ai_tasks.ModelClient, 'complete', complete)
    manager = ai_tasks.TaskManager(); manager.launch(task())
    assert entered.wait(2)
    manager.interrupt('assign-1')
    assert wait_job(manager)['state'] == 'interrupted'
    manager.interrupt('assign-1')
    assert manager.get_job('assign-1')['state'] == 'interrupted'


def test_attachments_and_legacy_migration(tmp_path):
    root = tmp_path / 'workspace' / '历史作业'; root.mkdir(parents=True)
    storage.write('codex-jobs.json', {'assign-1': {'task_id': 'assign-1', 'title': '历史作业', 'workspace': str(root),
        'thread_id': 'old-codex-thread', 'entries': [{'role': 'user', 'text': '题目'}]}})
    manager = ai_tasks.TaskManager()
    assert 'thread_id' not in manager.jobs['assign-1'] and manager.jobs['assign-1']['messages'][0]['content'] == '题目'
    source = tmp_path / '说明.txt'; source.write_text('hello', encoding='utf-8')
    records = manager.attach_files('assign-1', [str(source), str(source)])
    assert records[0]['path'] != records[1]['path']
    assert all((root / record['path']).is_file() for record in records)


def test_real_http_stream_to_file(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            if len(seen) == 1:
                call = tool_call('write_file', {'path': 'outputs/HTTP.txt', 'content': '真实 HTTP 流'})
                packets = [event({'tool_calls': [call | {'index': 0}]}), event({}, 'tool_calls')]
            else:
                packets = [event({'content': '完成'}), event({}, 'stop')]
            for packet in packets + [b'data: [DONE]']:
                raw = packet + b'\n\n'
                for i in range(0, len(raw), 7):
                    self.wfile.write(raw[i:i + 7]); self.wfile.flush()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        root = tmp_path / 'workspace' / 'http'; root.mkdir(parents=True)
        manager = ai_tasks.TaskManager()
        job = {'task_id': 'http', 'workspace': str(root), 'messages': [{'role': 'user', 'content': '创建结果文件'}], 'entries': []}
        manager.jobs['http'] = job
        client = ai_models.ModelClient(CONFIG | {'base_url': f'http://127.0.0.1:{server.server_port}/v1'})
        manager._turn(job, client, ai_tools.ToolKit(root), threading.Event(), 3)
        assert (root / 'outputs/HTTP.txt').read_text(encoding='utf-8') == '真实 HTTP 流'
        assert seen[1]['messages'][-1]['role'] == 'tool'
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def test_reasoning_content_preserved_for_compatible_models(monkeypatch):
    monkeypatch.setattr(ai_models.requests, 'post', lambda *a, **k: Response(lines=[
        event({'reasoning_content': '检查文件'}), event({'content': '答案'}), event({}, 'stop')]))
    answer = ai_models.ModelClient(CONFIG).complete([], [], threading.Event())
    assert answer['reasoning_content'] == '检查文件'


def test_symlink_escape(kit, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.mkdir()
    try:
        (kit.root / 'escape').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('当前系统不允许创建符号链接')
    assert 'error' in kit.execute('write_file', '{"path":"escape/secret","content":"x"}')
    assert not (outside / 'secret').exists()


def test_document_cannot_be_plain_text(kit):
    assert 'error' in kit.execute('write_file', '{"path":"fake.docx","content":"hi"}')


def test_redirect_to_private_network_is_rejected(kit, monkeypatch):
    class Session:
        trust_env = True
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def mount(self, *args): pass
        def get(self, url, **kwargs):
            response = Response()
            response.is_redirect = True
            response.headers = {'Location': 'http://127.0.0.1/secret'}
            return response
    monkeypatch.setattr(ai_tools.requests, 'Session', Session)
    monkeypatch.setattr(ai_tools.socket, 'getaddrinfo', lambda host, *a, **k: [(2, 1, 6, '',
        ('127.0.0.1' if host == '127.0.0.1' else '93.184.216.34', 80))])
    with pytest.raises(ValueError, match='内网'):
        kit._fetch('https://example.com')


def test_web_connection_pins_validated_ip_and_keeps_tls_hostname(monkeypatch):
    import requests
    monkeypatch.setattr(ai_tools.ToolKit, '_public_url', lambda url: '93.184.216.34')
    adapter = ai_tools.PublicWebAdapter()
    try:
        request = requests.Request('GET', 'https://example.com/page').prepare()
        pool = adapter.get_connection_with_tls_context(request, True)
        assert pool.host == '93.184.216.34'
        assert pool.assert_hostname == 'example.com'
        assert pool.conn_kw['server_hostname'] == 'example.com'
        assert request.headers['Host'] == 'example.com'
    finally:
        adapter.close()


def test_provider_profiles_keep_separate_keys_and_restore_legacy(tmp_path):
    from ai_presets import preset_configuration
    legacy = CONFIG | {'base_url': 'https://api.deepseek.com/v1', 'model': 'old-custom-model'}
    storage.write('ai-model.dpapi', legacy, secret=True)
    assert ai_models.configuration()['provider'] == 'deepseek'
    ai_models.save_configuration(preset_configuration('openai') | {'api_key': 'gpt-secret'})
    profiles = ai_models.model_profiles()
    assert profiles['deepseek']['api_key'] == 'test-secret'
    assert profiles['deepseek']['model'] == 'old-custom-model'
    assert profiles['openai']['api_key'] == 'gpt-secret'
    ai_models.save_configuration(profiles['deepseek'])
    assert ai_models.configuration()['provider'] == 'deepseek'
    assert ai_models.model_profiles()['openai']['api_key'] == 'gpt-secret'
    raw = (tmp_path / 'ai-model.dpapi').read_bytes()
    assert b'gpt-secret' not in raw and b'test-secret' not in raw
    assert 'profiles' not in ai_models.configuration()


def test_gemini_stream_preserves_tool_signature(monkeypatch):
    signature = {'google': {'thought_signature': 'opaque-provider-signature'}}
    monkeypatch.setattr(ai_models.requests, 'post', lambda *a, **k: Response(lines=[
        event({'tool_calls': [tool_call('list_files', {}) | {'index': 0}]}),
        event({'tool_calls': [{'index': 0, 'extra_content': signature}]}), event({}, 'tool_calls')]))
    answer = ai_models.ModelClient(CONFIG).complete([], [], threading.Event())
    assert answer['tool_calls'][0]['extra_content'] == signature


@pytest.mark.parametrize('provider', ['deepseek', 'openai', 'anthropic', 'kimi', 'kimi_global', 'gemini', 'grok'])
def test_preset_request_url_and_auth(provider, monkeypatch):
    from ai_presets import preset_configuration
    cfg = preset_configuration(provider) | {'api_key': 'provider-key'}
    seen = {}
    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Response(result={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'content': '', 'tool_calls': [tool_call('list_files', {})]}}]})
    monkeypatch.setattr(ai_models.requests, 'post', post)
    ai_models.ModelClient(cfg).complete([], ai_tools.SCHEMAS, threading.Event())
    assert seen['url'] == cfg['base_url'] + '/chat/completions'
    assert seen['headers']['Authorization'] == 'Bearer provider-key'
    assert seen['json']['model'] == cfg['model']
    assert 'provider' not in seen['json'] and 'profiles' not in seen['json']
