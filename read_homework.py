"""Read a single assignment and download its NJU-hosted attachments with saved session."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
from moodle import Moodle, safe_url, soup, text


def read_assignment(url, out):
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
        url = a['href']
        if safe_url(url) and 'pluginfile.php' in url:
            result = client.request('GET', url)
            if 'text/html' in result.headers.get('Content-Type', ''):
                continue
            suffix = Path(urlparse(url).path).suffix
            if len(suffix) > 12 or not suffix.replace('.', '').isalnum():
                suffix = '.bin'
            name = f'attachment-{i+1}{suffix}'
            (out / name).write_bytes(result.content)
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
