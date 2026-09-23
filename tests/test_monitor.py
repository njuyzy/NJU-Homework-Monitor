from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import time
import pytest
import storage
import moodle
import app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    monkeypatch.setattr(app, 'login_process', lambda: SimpleNamespace(poll=lambda:None))
    app.login_handle = None
    app.state.update(phase='connected', last_error=None, notification_error=None)


def assignment_html(status='草稿（未提交）', due='2026年09月10日 星期四 23:59'):
    return f'''<main id="region-main"><h2>算法作业 1</h2><div id="intro">证明并实现算法。
      <a href="https://selearning.nju.edu.cn/pluginfile.php/1/task.pdf">题目 PDF</a>
      <a href="javascript:alert(1)">bad</a></div><table><tr><th>提交状态</th><td>{status}</td></tr>
      <tr><th>截止时间</th><td>{due}</td></tr></table></main>'''


def task(**kwargs):
    return {'id':'assign-1','course_id':'3','course':'课程','title':'作业','url':moodle.BASE+'/mod/assign/view.php?id=1',
            'status':'pending','due':'2026-09-10T23:59:00+08:00','due_raw':'明晚',**kwargs}


@pytest.mark.parametrize('value,expected', [('草稿（未提交）','draft'),('尚未提交','pending'),('No submissions have been made yet','pending'),
 ('Draft (not submitted)','draft'),('Submitted for grading','submitted'),('提交以供评分','submitted'),('已评分','unknown')])
def test_status(value, expected):
    assert moodle.submission_state(value) == expected


def test_assignment_dates_attachments_and_status():
    t=moodle.parse_assignment(assignment_html(),moodle.BASE+'/mod/assign/view.php?id=42',{'id':'3','name':'算法'})
    assert t['due']=='2026-09-10T23:59:00+08:00'
    assert t['status']=='draft'
    assert len(t['attachments'])==1
    assert t['id']=='assign-42'


def test_live_nju_chinese_submission_labels():
    html=assignment_html(status='没有尝试',due='2026年09月9日 Wednesday 00:00').replace('提交状态','作业状态').replace('截止时间','到期日期')
    t=moodle.parse_assignment(html,moodle.BASE+'/mod/assign/view.php?id=5509',{'id':'417','name':'C++高级程序设计'})
    assert t['status']=='pending'
    assert t['due']=='2026-09-09T00:00:00+08:00'


def test_unrecognized_due_is_not_silently_no_due():
    t=moodle.parse_assignment(assignment_html(due='下周四结束'),moodle.BASE+'/mod/assign/view.php?id=42',{'id':'3','name':'算法'})
    assert t['due'] is None and t['warning'] and t['due_raw']=='下周四结束'


def test_activity_dates_use_deadline_instead_of_opening_date():
    html = '''<main id="region-main"><h2>作业</h2>
        <div class="activity-dates">开放：2026年9月1日 08:00 截止：2026年9月30日 23:59</div>
        <table><tr><th>Submission Status</th><td>Submitted for grading</td></tr></table></main>'''
    task = moodle.parse_assignment(html, moodle.BASE + '/mod/assign/view.php?id=42', {'id': '3', 'name': '算法'})
    assert task['due'] == '2026-09-30T23:59:00+08:00'
    assert task['status'] == 'submitted'


def test_login_html_never_becomes_assignment():
    with pytest.raises(moodle.ParseError):
        moodle.parse_assignment('<input type=password>',moodle.BASE+'/mod/assign/view.php?id=1',{'id':'3','name':'课程'})


@pytest.mark.parametrize('url',[ 'http://selearning.nju.edu.cn/a', 'https://selearning.nju.edu.cn.evil.test/a',
 'https://selearning.nju.edu.cn@evil.test/a', 'javascript:alert(1)', 'https://selearning.nju.edu.cn:8443/a'])
def test_url_boundary(url):
    assert not moodle.safe_url(url)


def test_reminder_boundaries():
    due=datetime.fromisoformat(task()['due'])
    assert app.reminder_bucket(task(),due-timedelta(hours=23),[72,24,6,1])=='24'
    assert app.reminder_bucket(task(),due-timedelta(hours=5),[72,24,6,1])=='6'
    assert app.reminder_bucket(task(),due,[72,24,6,1]).startswith('overdue:')
    assert app.reminder_bucket(task(status='submitted'),due,[24]) is None
    assert app.reminder_bucket(task(stale=True),due,[24]) is None
    assert app.reminder_bucket(task(status='unknown'),due,[24]) is None


def test_dedup_and_failed_notification_retry(monkeypatch):
    now=datetime.now(moodle.TZ)
    t=task(due=(now+timedelta(hours=5)).isoformat())
    snapshot={'tasks':[t],'last_sync':now.isoformat()}
    sent=[]
    def fail(*args):
        raise OSError('notifications offline')
    monkeypatch.setattr(app,'notify',fail)
    app.send_reminders(snapshot)
    assert storage.read('reminders.json',{})=={}
    monkeypatch.setattr(app,'notify',lambda *args:sent.append(args))
    app.send_reminders(snapshot);app.send_reminders(snapshot)
    assert len(sent)==1
    t['due']=(now+timedelta(hours=4)).isoformat()
    app.send_reminders(snapshot)
    assert len(sent)==2 # A changed deadline must produce a fresh reminder.


def test_partial_sync_retains_unread_old_task(monkeypatch):
    old={'tasks':[task()],'last_sync':'2026-09-09T00:00:00+08:00'}
    storage.write('snapshot.json',old)
    result={'tasks':[],'courses':[],'errors':[{'course_id':'3','message':'network error'}]}
    monkeypatch.setattr(app,'Moodle',lambda:SimpleNamespace(collect=lambda progress:result))
    app.sync()
    saved=storage.read('snapshot.json')
    assert saved['tasks'][0]['stale'] is True
    assert app.state['phase']=='partial'


def test_failed_sync_keeps_snapshot(monkeypatch):
    old={'tasks':[task()],'last_sync':'2026-09-09T00:00:00+08:00'}
    storage.write('snapshot.json',old)
    def fail(progress):
        raise moodle.LoginRequired('需要验证')
    monkeypatch.setattr(app,'Moodle',lambda:SimpleNamespace(collect=fail))
    app.sync()
    assert storage.read('snapshot.json')==old
    assert app.state['phase']=='login_required'


def test_dpapi_round_trip_and_no_plaintext():
    storage.write('test.dpapi',{'secret':'local-test-secret'},secret=True)
    assert b'local-test-secret' not in (storage.DATA/'test.dpapi').read_bytes()
    assert storage.read('test.dpapi',secret=True)=={'secret':'local-test-secret'}


def test_portable_secret_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    raw=b'portable-local-secret'
    protected=storage.protect(raw, platform='posix')
    assert raw not in protected
    assert protected.startswith(storage.PORTABLE_MAGIC)
    assert storage.protect(protected, decrypt=True, platform='posix')==raw
    assert (tmp_path/'.secret.key').exists()


def test_course_pagination(monkeypatch):
    pages=[]
    for start, count in ((1,50),(51,2)):
        pages.append([{'data':{'courses':[{'id':i,'fullname':f'课程{i}','viewurl':f'{moodle.BASE}/course/view.php?id={i}'} for i in range(start,start+count)],'nextoffset':start+count-1},'error':False}])
    client=moodle.Moodle()
    offsets=[]
    def request(method,url,**kwargs):
        offsets.append(kwargs['json'][0]['args']['offset'])
        return SimpleNamespace(json=lambda:pages.pop(0))
    monkeypatch.setattr(client,'request',request)
    assert len(client.courses('M.cfg={"sesskey":"fake-key"}'))==52
    assert offsets==[0,50]


def test_cross_origin_credential_redirect_blocked(monkeypatch):
    client=moodle.Moodle()
    sent=[]
    def request(method,url,**kwargs):
        sent.append(url)
        return SimpleNamespace(status_code=307,headers={'Location':'https://evil.test/login'},close=lambda:None)
    monkeypatch.setattr(client.session,'request',request)
    with pytest.raises(moodle.LoginRequired):
        client.request('POST',moodle.AUTH+'/authserver/login',data={'password':'test-secret'})
    assert len(sent)==1


def test_expired_login_opens_browser_once_until_reconnected(monkeypatch):
    opened=[]
    monkeypatch.setattr(app,'login_process',lambda:(opened.append(True) or SimpleNamespace(poll=lambda:0)))
    assert app.open_login_once() is True
    assert app.open_login_once() is False
    assert len(opened)==1
    storage.write('login-prompt.json',{'prompted':False})
    assert app.open_login_once() is True
    assert len(opened)==2


def test_manual_login_can_reopen_closed_window(monkeypatch):
    monkeypatch.setattr(app,'login_process',lambda:SimpleNamespace(poll=lambda:0))
    storage.write('login-prompt.json',{'prompted':True})
    assert app.open_login_once(manual=True) is True


def test_semester_scope_and_manual_course_override(monkeypatch):
    now=datetime.now(moodle.TZ)
    boundary=datetime(now.year,7 if now.month>=7 else 1,1,tzinfo=moodle.TZ)
    courses=[{'id':'1','name':'本学期','url':moodle.BASE+'/course/view.php?id=1','start':boundary.timestamp(),'end':0},
             {'id':'2','name':'历史课','url':moodle.BASE+'/course/view.php?id=2','start':(boundary-timedelta(days=90)).timestamp(),'end':(now+timedelta(days=365)).timestamp()}]
    client=moodle.Moodle()
    monkeypatch.setattr(client,'login',lambda:SimpleNamespace(text='dashboard'))
    monkeypatch.setattr(client,'courses',lambda html:courses)
    monkeypatch.setattr(client,'page',lambda url:'<main></main>')
    monkeypatch.setattr(client,'save_session',lambda:None)
    first=client.collect()
    assert [c['id'] for c in first['courses'] if c['monitored']]==['1']
    storage.write('settings.json',{'included_courses':['2'],'excluded_courses':['1']})
    second=client.collect()
    assert [c['id'] for c in second['courses'] if c['monitored']]==['2']
