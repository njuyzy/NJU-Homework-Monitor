"""Read-only Moodle collector and NJU CAS login adapter."""
import base64
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import storage

BASE = 'https://selearning.nju.edu.cn'
AUTH = 'https://authserver.nju.edu.cn'
LOGIN = BASE + '/login/index.php?authCAS=CAS'
TZ = timezone(timedelta(hours=8))
ALLOWED = {'selearning.nju.edu.cn', 'authserver.nju.edu.cn'}


class LoginRequired(RuntimeError):
    pass


class ParseError(RuntimeError):
    pass


def soup(html):
    return BeautifulSoup(html, 'html.parser')


def text(node):
    return node.get_text(' ', strip=True) if node else ''


def safe_url(url):
    p = urlparse(url)
    return p.scheme == 'https' and p.hostname == 'selearning.nju.edu.cn' and not p.username and p.port in (None, 443)


def date_value(value):
    """Moodle Chinese/English date labels. All dates use Asia/Shanghai."""
    if not value:
        return None
    m = re.search(r'(20\d{2})\s*[年/\-]\s*(\d{1,2})\s*[月/\-]\s*(\d{1,2})\s*日?.*?(\d{1,2}):(\d{2})(?::(\d{2}))?', value)
    if m:
        y, mo, d, h, mi, se = m.groups()
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(se or 0), tzinfo=TZ).isoformat()
    clean = re.sub(r'^[A-Za-z]+,\s*', '', value).strip()
    for fmt in ('%d %B %Y, %I:%M %p', '%d %B %Y, %H:%M', '%B %d, %Y, %I:%M %p'):
        try:
            return datetime.strptime(clean, fmt).replace(tzinfo=TZ).isoformat()
        except ValueError:
            pass
    return None


def submission_state(value):
    v = value.casefold()
    # Negative and draft states MUST precede substring matching for submitted.
    if any(t in v for t in ('草稿', 'draft', '尚未提交', '未提交', '没有提交', '没有尝试', '未作答', 'no attempt', 'no submission', 'not submitted', '未交')):
        return 'draft' if ('草稿' in v or 'draft' in v) else 'pending'
    if any(t in v for t in ('已提交', '提交以供评分', '提交以备评分', 'submitted for grading', 'submitted', '已完成', '已结束', 'finished')):
        return 'submitted'
    return 'unknown'


def parse_assignment(html, url, course):
    doc = soup(html)
    region = doc.select_one('#region-main')
    if region is None or doc.select_one('input[type=password]'):
        raise ParseError('作业详情页面结构不符合预期。')
    rows = {}
    for tr in region.select('tr'):
        cols = tr.select('th, td')
        if len(cols) >= 2:
            rows[text(cols[0]).rstrip(':：')] = text(cols[1])
    status_raw = next((v for k, v in rows.items() if k in ('作业状态', '提交状态', 'Submission status')), '')
    # Comment widgets contain hidden templates; omit them from the useful status details.
    rows.pop('提交评论', None)
    due_raw = next((v for k, v in rows.items() if any(t in k.lower() for t in ('截止', '到期', 'due date'))), '')
    desc = region.select_one('#intro')
    title = text(region.select_one('h2')) or text(doc.select_one('h1'))
    kind = 'quiz' if '/mod/quiz/' in url else 'assign'
    if kind == 'quiz':
        statuses = [submission_state(text(c)) for c in region.select('table.quizattemptsummary td')]
        if 'submitted' in statuses:
            status_raw = '已完成'
        elif region.select_one('form[action*="startattempt"]'):
            status_raw = '未作答'
    if not due_raw:
        for node in region.select('[data-region="activity-dates"], .activity-dates, .quizinfo p'):
            value = text(node)
            if any(t in value.lower() for t in ('截止', '关闭', '关闭于', 'due:', 'closes')):
                due_raw = value
    due = date_value(due_raw)
    attachments = []
    for a in (desc or region).select('a[href]'):
        href = urljoin(url, a['href'])
        if safe_url(href) and ('pluginfile.php' in href or '/resource/' in href):
            attachments.append({'name': text(a) or '附件', 'url': href})
    cmid = parse_qs(urlparse(url).query).get('id', [''])[0]
    if not title or not cmid.isdigit():
        raise ParseError('无法识别作业标题或编号。')
    return {'id': f'{kind}-{cmid}', 'title': title, 'url': url, 'kind': kind,
            'course_id': str(course['id']), 'course': course['name'],
            'status': submission_state(status_raw), 'status_raw': status_raw or '需要核实',
            'due': due, 'due_raw': due_raw, 'description': text(desc),
            'attachments': attachments, 'details': rows,
            'warning': '截止时间格式未识别，请打开原网页核实。' if due_raw and not due else '',
            'checked_at': datetime.now(TZ).isoformat()}


def encrypt_password(password, salt):
    if not salt or len(salt.encode()) not in (16, 24, 32):
        raise LoginRequired('统一认证加密格式已变化，请使用网页登录。')
    prefix = ''.join(secrets.choice('ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678') for _ in range(64))
    padder = padding.PKCS7(128).padder()
    raw = padder.update((prefix + password).encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(salt.encode()), modes.CBC(secrets.token_bytes(16))).encryptor()
    return base64.b64encode(encryptor.update(raw) + encryptor.finalize()).decode()


class Moodle:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'Mozilla/5.0 NJUHomeworkMonitor/1.0 (personal read-only monitor)'
        try:
            saved_cookies = storage.read('cookies.dpapi', [], secret=True)
        except Exception:
            saved_cookies = []
        for c in saved_cookies:
            self.session.cookies.set_cookie(requests.cookies.create_cookie(**c))

    def save_session(self):
        cookies = [{k: getattr(c, k) for k in ('name', 'value', 'domain', 'path', 'secure', 'expires')}
                   for c in self.session.cookies if c.domain.lstrip('.') in ALLOWED]
        storage.write('cookies.dpapi', cookies, secret=True)

    def request(self, method, url, **kwargs):
        # Validate EVERY redirect before following, including credential-bearing POSTs.
        for _ in range(12):
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.hostname not in ALLOWED or parsed.port not in (None, 443) or parsed.username:
                raise LoginRequired('网站跳转到新的认证地址，请在浏览器中核实。')
            response = self.session.request(method, url, timeout=(12, 30), allow_redirects=False, **kwargs)
            if response.status_code in (301, 302, 303, 307, 308):
                response.close()
                dest = urljoin(url, response.headers['Location'])
                if response.status_code in (301, 302, 303):
                    method = 'GET'
                    kwargs.pop('data', None)
                    kwargs.pop('json', None)
                elif urlparse(dest).hostname != parsed.hostname:
                    raise LoginRequired('拒绝跨域转发认证表单。')
                kwargs.pop('params', None)
                url = dest
                continue
            response.raise_for_status()
            response.encoding = 'utf-8'
            return response
        raise LoginRequired('统一认证跳转次数过多。')

    @staticmethod
    def logged_in(response):
        doc = soup(response.text)
        return urlparse(response.url).hostname == 'selearning.nju.edu.cn' and bool(doc.select_one('a[href*="/login/logout.php"]'))

    def login(self):
        current = self.request('GET', BASE + '/my/')
        if self.logged_in(current):
            return current
        try:
            cred = storage.read('credentials.dpapi', None, secret=True)
        except Exception as e:
            raise LoginRequired(str(e)) from e
        if not cred:
            raise LoginRequired('请连接南京大学统一身份认证。')
        page = self.request('GET', LOGIN)
        if self.logged_in(page):
            return page
        doc = soup(page.text)
        form = doc.select_one('form#pwdFromId')
        if not form:
            raise LoginRequired('需要在登录窗口完成身份验证。')
        # Respect the site's mandatory CAPTCHA flow instead of posting around it.
        forced = bool(re.search(r'var\s+_badCredentialsCount\s*=\s*[\"\']0[\"\']', page.text))
        check = self.request('GET', AUTH + '/authserver/checkNeedCaptcha.htl', params={'username': cred['username']})
        if forced or check.json().get('isNeed'):
            raise LoginRequired('学校要求滑块验证，请点“连接账号”完成验证。')
        payload = {n['name']: n.get('value', '') for n in form.select('input[name][type=hidden]')}
        payload.update(username=cred['username'], password=encrypt_password(cred['password'], form.select_one('#pwdEncryptSalt')['value']))
        result = self.request('POST', page.url, data=payload)
        if not self.logged_in(result):
            raise LoginRequired('统一认证未完成，请点“连接账号”检查验证码或账号状态。')
        self.save_session()
        return self.request('GET', BASE + '/my/')

    def page(self, url):
        result = self.request('GET', url)
        if not self.logged_in(result):
            raise LoginRequired('登录已过期，请重新连接账号。')
        return result.text

    def courses(self, dashboard):
        match = re.search(r'[\"\']sesskey[\"\']\s*:\s*[\"\']([^\"\']+)', dashboard)
        if not match:
            raise ParseError('无法读取课程会话，请重新连接账号。')
        all_courses = {}
        offset = 0
        for _ in range(30):
            args = {'classification': 'all', 'limit': 50, 'offset': offset, 'sort': 'fullname'}
            result = self.request('POST', BASE + '/lib/ajax/service.php', params={'sesskey': match[1]},
                                  json=[{'index': 0, 'methodname': 'core_course_get_enrolled_courses_by_timeline_classification', 'args': args}]).json()
            if not isinstance(result, list) or not result or result[0].get('error'):
                raise ParseError('课程接口未成功返回，保留上次同步数据。')
            data = result[0]['data']
            batch = data['courses']
            for c in batch:
                if safe_url(c.get('viewurl', '')):
                    all_courses[str(c['id'])] = {'id': str(c['id']), 'name': text(soup(c['fullname'])),
                       'url': c['viewurl'], 'start': c.get('startdate'), 'end': c.get('enddate')}
            next_offset = int(data.get('nextoffset', offset + len(batch)))
            if len(batch) < 50:
                return list(all_courses.values())
            if next_offset <= offset:
                raise ParseError('课程分页未推进，保留上次同步数据。')
            offset = next_offset
        raise ParseError('课程分页超过安全上限，保留上次同步数据。')

    def collect(self, progress=lambda _: None):
        dashboard = self.login()
        courses = self.courses(dashboard.text)
        excluded = set(storage.settings()['excluded_courses'])
        included = set(storage.settings()['included_courses'])
        tasks, errors = [], []
        today = datetime.now(TZ)
        now = today.timestamp()
        # NJU's exported end dates span a full year even for semester-long courses.
        # Use the start-date cohort, and let users explicitly include earlier courses.
        semester_start = datetime(today.year, 7 if today.month >= 7 else 1, 1, tzinfo=TZ).timestamp()
        for i, course in enumerate(courses):
            course['current'] = (not course['start'] or semester_start <= course['start'] <= now) and (not course['end'] or course['end'] >= now)
            course['monitored'] = course['id'] not in excluded and (course['current'] or course['id'] in included)
            if not course['monitored']:
                continue
            progress(f'正在检查 {course["name"]}（{i+1}/{len(courses)}）')
            try:
                doc = soup(self.page(course['url']))
                links = {}
                for a in doc.select('a[href]'):
                    href = urljoin(BASE, a['href'])
                    if safe_url(href) and re.search(r'/mod/(assign|quiz)/view\.php\?id=\d+', href):
                        p = urlparse(href)
                        canonical = BASE + p.path + '?id=' + parse_qs(p.query)['id'][0]
                        links[canonical] = text(a)
                for url in links:
                    try:
                        tasks.append(parse_assignment(self.page(url), url, course))
                    except LoginRequired:
                        raise
                    except Exception as e:
                        errors.append({'course_id': course['id'], 'url': url, 'message': f'{links[url]}：读取失败（{type(e).__name__}）'})
            except LoginRequired:
                raise
            except Exception as e:
                errors.append({'course_id': course['id'], 'message': f'{course["name"]}：读取失败（{type(e).__name__}）'})
        self.save_session()
        return {'courses': courses, 'tasks': tasks, 'errors': errors}
