# 课后 · 南大作业提醒

可在 Windows、macOS 和常见桌面 Linux 发行版运行的本机桌面应用。核心作业监控和 AI 任务功能跨平台，系统通知与开机自启会根据设备能力降级。

## 使用

1. 启动桌面应用：Windows 可双击打包好的 `NJU-Homework-Monitor.exe`；开发环境可运行 `python desktop_app.py`。
2. 点击“连接账号”，在打开的浏览器窗口完成南京大学统一认证和滑块验证。成功后自动关闭窗口并同步。
3. “作业总览”展示作业/测验链接、课程、截止时间和提交状态；点击标题查看正文、附件和状态详情。草稿仍算未完成，未知状态单独标识。
4. “提醒设置”可开关持续监控、开机自启、桌面通知，修改检查间隔和提醒时间。Windows 支持应用内设置开机自启；其他系统会显示对应能力状态。
5. 先在“提醒设置 → 配置自定义 AI 模型”选择 DeepSeek、GPT、Claude、Kimi、Gemini 或 Grok，自动填入官方 API 地址与默认模型；填写自己的 API Key，测试连接后保存。也可选择“自定义”手动配置。任务页“更多接口”中也可打开设置。
6. 点击“AI 一键完成”，应用直接调用配置的模型，在“AI 任务”页查看流式输出、工具活动和生成文件。可补充要求、添加附件、停止或继续处理；每份作业独立运行，运行中发送的要求自动排队。停止时保留尚未处理的队列，点击“继续处理”恢复。

顶部“待完成”“72 小时内截止”“已逾期”“已提交”统计卡片可点击筛选，选中卡片会高亮；筛选可与课程、搜索组合使用，下拉框还可选择全部、草稿或待核实。待完成包含未提交、草稿、逾期和待核实。未提交或草稿到达截止时间后自动显示“已逾期”，已提交和待核实不会仅因日期过期而改变状态。

应用启动时及每 15 分钟在后台检查 GitHub `main` 分支。发现相对当前构建的新提交后，主页显示更新按钮；安装包尚未发布时显示“发现更新 · 打包中”，发布后显示“更新可用”。点击后可打开 GitHub 发布页下载新版，关闭旧程序后替换 EXE，并保留同目录 `data/` 文件夹。网络失败不会阻塞作业同步，下次检查自动重试。

仓库内的 `.github/workflows/release.yml` 会在推送到 `main` 后运行测试、打包并创建 `build-<完整提交 SHA>` 发布。需在 GitHub 启用 Actions 并允许工作流写入仓库内容。`build_exe.ps1` 将提交编号嵌入 EXE，因此用户电脑无需安装 Git；旧版 EXE 必须先手动替换为包含此功能的版本，才能收到后续更新提醒。更新按钮提供下载入口，不会自动覆盖正在运行的程序。

AI 工作文件位于 `data/workspace/<作业名>/`。应用写入作业信息与附件清单，AI 按需使用学校读取工具获取正文和附件到 `source/`，上传的文件位于 `uploads/`，最终文件放在 `outputs/`。输出文件会出现在任务页右侧，双击打开。学校读取失败时可手动添加题目附件。

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
- 监控状态、任务历史与工作文件保存在本机 `data/`。模型配置及 API Key 加密存储在 `data/ai-model.dpapi`。使用 AI 时，对话、题目和工具读取的内容会发送至你配置的模型服务；学校密码和 Cookie 不会交给模型。
- `data/`、`.venv/`、`assignments/` 已加入 `.gitignore`，分享项目时不要携带这些目录。

## 自定义模型与工具调用

AI 模块不再依赖 Codex、CLI 或 App Server。应用自行完成“模型回复 → 校验工具参数 → 执行工具 → 回传结果 → 再次调用模型”的循环。

- API 地址：填写兼容 Chat Completions 的基础地址（包括服务要求的 `/v1` 等路径），或完整 `/chat/completions` 地址。
- 模型名称：可从下拉列表选择，也可直接输入其他模型 ID。模型与网关均需支持 `tools` / `tool_calls`。Claude、Gemini 预设使用各自官方的 Chat Completions 兼容入口；原生 Messages / generateContent 协议不直接接入。
- API Key：本机加密保存；无鉴权的本地服务可留空。API 地址允许配置本地模型服务器。
- 支持流式与非流式输出、视觉输入开关、请求超时和每轮工具循环上限。设置变更用于新启动或继续的任务，运行中的任务使用启动时配置。
- 连接测试同时检查模型能否返回工具调用，不会实际读取文件。停止会阻止后续工具调用，正在等待的请求受超时控制。
- 任务历史写入 `data/ai-jobs.json`；首次启动会保留旧 `codex-jobs.json` 的对话与工作目录，新任务不再使用旧服务。

内置预设（2026-09-17 核对；模型是否可用取决于账号权限和服务商）：

| 服务商 | 自动填写地址 | 默认模型 | 官方文档 |
| --- | --- | --- | --- |
| DeepSeek | `https://api.deepseek.com` | `deepseek-flash` | [快速开始](https://api-docs.deepseek.com/) |
| GPT / OpenAI | `https://api.openai.com/v1` | `gpt-5.4` | [模型说明](https://developers.openai.com/api/docs/models/gpt-5.4) |
| Claude | `https://api.anthropic.com/v1` | `claude-sonnet-5` | [兼容接口](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)、[模型列表](https://platform.claude.com/docs/en/models/overview) |
| Kimi 国内 | `https://api.moonshot.cn/v1` | `kimi-k3` | [快速开始](https://platform.kimi.com/docs/get-api-key) |
| Kimi 国际 | `https://api.moonshot.ai/v1` | `kimi-k3` | [快速开始](https://platform.kimi.ai/docs/overview) |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-3.8-flash` | [兼容接口](https://ai.google.dev/gemini-api/docs/openai) |
| Grok | `https://api.x.ai/v1` | `grok-4.6` | [模型与接口](https://docs.x.ai/developers/grok-4-6) |

切换服务商会恢复该服务商已保存的地址、模型和密钥；首次选择自动使用预设。地址与模型始终可编辑，打开设置不会重置旧配置。对话框中切换会暂存未保存的编辑，点击“保存配置”仅保存当前服务商并设为活动配置，取消不会改动磁盘。各服务商配置统一加密保存在 `ai-model.dpapi`，密钥不会串用。Claude 官方兼容层只覆盖部分原生 API 功能，当前应用使用其中的文本、图片与函数工具调用能力。

| 工具 | 能力 |
| --- | --- |
| `list_files` | 浏览当前任务目录，分页列出文件 |
| `read_file` | 文本、代码、CSV、JSON 等按编码读取；PDF 文本层、DOCX 段落和表格、XLSX 单元格和公式、PPTX 文本和表格、ODF 正文、ZIP 清单自动提取 |
| `write_file` | 文本与通用 Base64 二进制写入／追加；按正文或结构化内容生成 DOCX、XLSX、PPTX、中文 PDF |
| `read_webpage` | GET 访问公开 HTTP(S) 链接，提取正文与链接，支持重定向与分页 |
| `download_file` | 下载非视频文件到当前任务目录 |
| `read_assignment` | 使用本机学校会话读取当前作业及附件，不向模型暴露凭据 |
| `run_code` | 运行任务目录内的 Python、JavaScript、C、C++ 或 Java 源码，支持参数和标准输入，返回编译／运行输出与退出码 |

所有非视频格式都有通用字节读写通道；这不表示模型能理解所有专有格式。图片会返回尺寸等信息，开启视觉输入后提供图像给支持视觉的模型。音频与未知二进制格式按 Base64 处理，不自动转写。扫描 PDF 无文本层时需另行 OCR；旧版 `.doc` / `.xls` / `.ppt` 可按字节读写，内容解析请先转为新版格式。结构化文档写入是重新生成，不保留原文件复杂排版。

文件工具限制在当前任务目录内，外部文件先通过附件按钮导入。单个附件、下载和写入文件上限 64 MB；更大文件可分块读取原始字节。视频扩展名、MIME 与常见视频容器会被拒绝。网页工具不携带模型密钥或学校 Cookie，拒绝本机／内网地址；不执行网页 JavaScript，动态网站或登录页面需导出资料后上传。工具不执行任意系统命令，也不会自动提交作业。

代码执行工具不接受任意 Shell 命令，单次最长 120 秒，标准输出和错误输出各保留前 64 KB，停止 AI 任务时会终止当前进程树。Python 使用应用内运行器，禁止联网、启动子进程以及读写任务目录外的普通文件；打包后的 EXE 无需另装 Python。JavaScript、C、C++、Java 分别需要本机安装 Node.js、GCC/Clang 或 JDK，这些本机运行时目前只有工作目录、超时、进程树与输出限制，不具备完整的操作系统级文件和网络沙箱，因此 AI 只会运行自己为当前作业编写或用户明确要求运行的源码，不会直接执行网页或附件中的未知代码。

## 开发与测试

环境：Python 3.11+、Chromium 系浏览器，以及支持工具调用的模型 API（仅 AI 任务需要）。

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
