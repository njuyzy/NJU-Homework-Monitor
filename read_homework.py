"""Read a single assignment and download its NJU-hosted attachments with saved session."""
import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse, unquote
from moodle import Moodle, safe_url, soup, text
from ai_tools import MAX_FILE, check_file_type
from ai_models import Cancelled


def read_assignment(url, out, cancel=None):
    out = Path(out)
    if not safe_url(url) or urlparse(url).path not in ('/mod/assign/view.php', '/mod/quiz/view.php'):
        raise ValueError('仅允许读取南大学习平台作业或测验页面。')
    client = Moodle()
    client.login()
    doc = soup(client.page(url))
    out.mkdir(parents=True, exist_ok=True)
    (out / '作业正文.txt').write_text(text(doc.select_one('#region-main')), encoding='utf-8')
    manifest = []
    intro = doc.select_one('#intro')
    for i, a in enumerate(intro.select('a[href]') if intro else []):
        if cancel and cancel.is_set():
            raise Cancelled()
        url = a['href']
        if safe_url(url) and 'pluginfile.php' in url:
            suffix = Path(unquote(urlparse(url).path)).suffix
            if len(suffix) > 12 or not suffix.replace('.', '').isalnum():
                suffix = '.bin'
            name = f'attachment-{i+1}{suffix}'
            try:
                check_file_type(name)
            except ValueError:
                continue
            with client.request('GET', url, stream=True) as result:
                mime = result.headers.get('Content-Type', '')
                if 'text/html' in mime:
                    continue
                try:
                    check_file_type(name, mime=mime)
                    raw = bytearray()
                    deadline = time.monotonic() + 90
                    for chunk in result.iter_content(65536):
                        if cancel and cancel.is_set():
                            raise Cancelled()
                        if time.monotonic() > deadline:
                            raise ValueError('附件下载超时。')
                        raw.extend(chunk)
                        check_file_type(name, raw[:32], mime)
                        if len(raw) > MAX_FILE:
                            raise ValueError('附件过大。')
                except ValueError:
                    continue
            (out / name).write_bytes(raw)
            manifest.append({'file': name, 'name': text(a), 'url': url})
    (out / 'attachments.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    manifest = read_assignment(args.url, args.out)
    print(f'已读取作业正文及 {len(manifest)} 个附件：{args.out}')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        raise SystemExit(f'读取失败（{type(e).__name__}），请在监控页面重新连接账号。')
