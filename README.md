# 课后 · 南大作业提醒

可在 Windows、macOS 和常见桌面 Linux 发行版运行的本机桌面应用。核心作业监控和 Codex 任务功能跨平台，系统通知与开机自启会根据设备能力降级。

## 使用

1. 启动桌面应用：Windows 可双击打包好的 `NJU-Homework-Monitor.exe`；开发环境可运行 `python desktop_app.py`。
2. 点击“连接账号”，在打开的浏览器窗口完成南京大学统一认证和滑块验证。成功后自动关闭窗口并同步。
3. “作业总览”展示作业/测验链接、课程、截止时间和提交状态；点击标题查看正文、附件和状态详情。草稿仍算未完成，未知状态单独标识。
4. “提醒设置”可开关持续监控、开机自启、桌面通知，修改检查间隔和提醒时间。Windows 支持应用内设置开机自启；其他系统会显示对应能力状态。
5. “AI 一键完成”调用已登录的 Codex 本机任务接口，并在后台运行，不会自动弹出 Codex。任务会进入左侧栏的“Codex 任务”工作区，可流式查看输出、补充要求、中断处理或打开工作目录。每项作业使用独立任务，可同时运行并随时切换；运行中发送的补充要求会自动排队。

Codex 工作文件位于 `data/workspace/<作业名>/`。应用会预先写入作业信息、原始数据、附件清单，并建立 `source/` 与 `outputs/`；生成或修改的文件会自动出现在任务页右侧，双击即可打开。

## 默认行为

- 每 30 分钟同步当前课程，截止前 72/24/6/1 小时提醒。逾期和未设置截止时间的未交作业每天提醒一次。
- 同一截止时间、同一提醒档位只提示一次。截止时间更改后重新计算提醒。
- 根据课程开始日期所属的半年识别本学期（当前为 2026 年秋季、7 月起）。学校导出的结束日期常覆盖全年，不能单独用于区分春秋学期。缺少开始日期的课程也纳入，避免漏掉；跨学期或历史课程可在“我的课程”手动加入或关闭。
- 网络或登录失败保留上次同步数据；部分作业失败时保留旧项并标注。未识别的截止日期展示原文，不推测时间。
- 只监控账号能看到的已发布作业和测验。老师未发布、按条件隐藏的项目无法提前发现。网站升级或自定义模块需要相应适配。
- 电脑关机、睡眠时不会运行。系统勿扰模式可能把提醒收进通知中心。下次开机同步后重新检查。

## 登录与隐私

- 密码与学校会话分别放在 `data/credentials.dpapi`、`data/cookies.dpapi`。Windows 使用 DPAPI；macOS/Linux 使用权限受限的本机密钥加密。
- 检测到登录失效后自动打开浏览器窗口，同一次失效只弹一次；登录成功后自动恢复同步。可在提醒设置关闭自动弹窗。关闭了窗口仍可手动点“连接账号”。
- 更换设备时建议只复制项目代码，不复制 `data/`。Windows DPAPI 数据无法在另一台设备解密，应用会提示在新设备重新保存凭据。
- 学校要求滑块验证时，必须在登录窗口操作；程序不会绕过验证码。保存会话不等于学校永不要求重新登录。
- 所有数据仅保存在本机 `data/` 目录，不向第三方服务发送学校密码。点击 AI 按钮会把对应作业内容交给你已登录的 Codex 处理。
- `data/`、`.venv/`、`assignments/` 已加入 `.gitignore`，分享项目时不要携带这些目录。

## Codex 接入

使用官方 App Server 的 `initialize → thread/start → turn/start`，保留用户默认模型，使用按需审批。Codex 的回复、处理状态和常见执行审批会同步到独立任务页；补充要求和中断操作分别使用 `turn/start` 与 `turn/interrupt`。若任务接口不可用，提供官方 `codex://new?path=...&prompt=...` 备用入口；备用入口只预填内容，需在 Codex 点击发送。登录、额度或模型错误可能导致生成失败，页面不会标记作业已提交。

官方参考：[App Server](https://learn.chatgpt.com/docs/app-server)、[深链参数](https://learn.chatgpt.com/docs/reference/commands#deep-links)。学校读取适配以当前统一认证页面和 Moodle 接口为基础。

## 开发与测试

环境：Python 3.11+、Chromium 系浏览器、已安装并登录的 Codex。Codex 命令需要位于 `PATH`；Windows 桌面版的内置命令也会自动识别。

Windows：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe desktop_app.py
```

macOS/Linux：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q
.venv/bin/python desktop_app.py
```

Linux 桌面通知需要系统提供 `notify-send`；macOS 使用系统自带的 `osascript`。缺少通知工具时，监控仍可正常运行，只会停用桌面通知。macOS/Linux 的开机自启需通过系统启动项手动配置。

`requirements.txt` 只声明运行时直接依赖，不固定版本；测试与打包工具放在 `requirements-dev.txt`。应用优先使用项目 `.venv`，找不到时会使用当前系统 Python。`data/service.log` 仅保存服务错误日志并限制滚动大小。更换项目路径后，重新开关一次“Windows 登录自启”以更新路径。

### 打包 exe（Windows）

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

产物在 `dist\NJU-Homework-Monitor.exe`。应用图标使用南京大学校徽。

## 退出与移除

“持续监控”关闭后暂停同步与提醒。关闭应用窗口即退出；后台监控线程随应用一并结束。需要移除时，退出后删除项目目录和快捷方式即可。
