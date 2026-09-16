"""User-assisted CAS login; keeps cookies encrypted, never exports passwords."""
import time
import re
from playwright.sync_api import sync_playwright
import storage
from moodle import BASE, LOGIN, ALLOWED


def run_login():
    stage = 'launch'
    storage.write('login-status.json', {'state': 'opening', 'message': '正在打开学校登录窗口…'})
    try:
        with sync_playwright() as p:
            browser = None
            for channel in ('msedge', 'chrome', None):
                try:
                    options = {'headless': False}
                    if channel:
                        options['channel'] = channel
                    browser = p.chromium.launch(**options)
                    break
                except Exception:
                    continue
            if browser is None:
                raise RuntimeError('未找到可用浏览器，请运行 playwright install chromium。')
            context = browser.new_context(locale='zh-CN')
            try:
                saved = storage.read('cookies.dpapi', [], secret=True)
            except Exception:
                saved = []
            if saved:
                context.add_cookies([{k: v for k, v in c.items() if v is not None} for c in saved])
            page = context.new_page()
            stage = 'navigation'
            try:
                page.goto(LOGIN, wait_until='domcontentloaded', timeout=60000)
            except Exception:
                # A slow tracking/QR resource must not destroy an otherwise usable login window.
                if not page.url.startswith(('https://authserver.nju.edu.cn/', BASE)):
                    raise
            stage = 'autofill'
            try:
                cred = storage.read('credentials.dpapi', None, secret=True)
            except Exception:
                cred = None
            if cred and page.url.startswith('https://authserver.nju.edu.cn/authserver/'):
                # Switch to the visible password form using the school's own tab.
                if not page.locator('#pwdFromId input[name=username]:visible').count():
                    for label in ('账号登录', '账户登录', '密码登录'):
                        candidate = page.get_by_text(label, exact=True).filter(visible=True)
                        if candidate.count() and candidate.first.is_visible():
                            candidate.first.click()
                            break
                username = page.locator('#pwdFromId input[name=username]:visible').first
                if username.is_visible():
                    username.fill(cred['username'])
                    pwd = page.locator('#pwdFromId input[type=password]:visible').first
                    pwd.click()
                    try:
                        pwd.fill(cred['password'], timeout=3000)
                    except Exception:
                        pass  # The user can type if the school's keyboard protection changes.
            storage.write('login-status.json', {'state': 'waiting', 'message': '请在浏览器窗口点击登录并完成学校的滑块验证。'})
            stage = 'verification'
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                if not context.pages:
                    raise RuntimeError('登录窗口已关闭，请重新连接。')
                for tab in context.pages:
                    if tab.url.startswith(BASE) and '/login/' not in tab.url:
                        if tab.locator('a[href*="/login/logout.php"]').count():
                            cookies = []
                            for c in context.cookies():
                                if c['domain'].lstrip('.') in ALLOWED:
                                    cookies.append({k: (None if k == 'expires' and c[k] == -1 else c[k])
                                                    for k in ('name', 'value', 'domain', 'path', 'secure', 'expires')})
                            storage.write('cookies.dpapi', cookies, secret=True)
                            storage.write('login-status.json', {'state': 'connected', 'message': '统一认证已连接，正在同步课程。'})
                            browser.close()
                            return
                page.wait_for_timeout(1000)
            raise RuntimeError('登录等待超时，请重新连接。')
    except Exception as e:
        # Browser exceptions can include form values or token-bearing URLs. Never persist them.
        message = re.sub(r'https?://\S+', '[学校地址]', str(e))
        try:
            credentials = storage.read('credentials.dpapi', {}, secret=True) or {}
        except Exception:
            credentials = {}
        for value in credentials.values():
            message = message.replace(value, '[已隐藏]')
        storage.write('login-status.json', {'state': 'error', 'message': '登录未完成或窗口已关闭，请重新连接。', 'error_type': type(e).__name__, 'stage': stage, 'diagnostic': message[:700]})


if __name__ == '__main__':
    run_login()
