# 网络延时助手 v2.0.1

Windows 原生 Tkinter 悬浮工具，标准库实现，无运行时第三方依赖。针对 Windows 10/11 x64、Python 3.12（含 Tcl/Tk）开发。

显示优化（2026-10-20）：启动 Tk 前启用 Windows 系统 DPI 感知；字体按系统 DPI 原生绘制，窗口、边距、图表坐标和事件列表同步缩放。默认不透明度改为 100%，首次升级迁移旧透明度和窗口坐标；之后保留用户自行调整的透明度。提亮辅助文字并加粗主读数。百度直连与 ChatGPT 代理的测速逻辑保持原样。

Tk 8.6 使用启动时的系统 DPI；在不同缩放比例的显示器之间移动窗口后，如清晰度变化，请在目标显示器上重启应用。

2026-10-21 修复：探测使用有固定并发上限的守护线程，关闭停止派发并取消排队任务；只读网络任务即使卡在 DNS，也不会阻止进程退出。直连和代理各自调度，每路完成后等待设置的间隔，不互相阻塞。本次运行计时不包含保存的历史。TLS/建连等待 15 秒，响应头读取等待 8 秒；失败细分为 TLS/建连/响应超时等。对照成功时单独提示，不把对照耗时冒充目标耗时。

浮窗只绘制本次启动后最近 5 分钟；详情回看可选择 5 分钟、30 分钟、2 小时。按时间和显示宽度分桶，中位线展示趋势、细竖线保留最小/最大范围、红线标失败，长时间未采样用暗色区域标记。两路浮窗纵轴各自适配，详情两张曲线共享纵轴。参考设计：[Network Monitor 的短时 sparkline](https://github.com/danielchalmers/Network-Monitor)、[SmokePing 的中位数与波动范围](https://oss.oetiker.ch/smokeping/doc/reading.en.html)。仅参考表现方式，未引入它们的代码或依赖。

## 文件

- `latency_desk.py`：悬浮窗、动态趋势、分层排查、事件 UI、配置和诊断 ZIP 导出。
- `diagnostics.py`：ICMP 网关、TCP 端口、HTTPS 探测、Windows 路由发现、只读 Codex SQLite 错误事件读取与本机保存。
- `probe_workers.py`：可停止派发的守护线程池。
- `latency_plot.py`：保留峰值/失败/空档的分桶曲线。
- `test_monitor.py`：挂起网络请求下的进程退出、密集曲线与空档测试。
- `test_diagnostics.py`：离线单元测试、Windows 本机回环测试及 Tkinter 功能测试。
- `latency.ico`：程序图标。
- `version_info.txt`、`requirements-build.txt`、`build.ps1`：Windows EXE 构建资源。

## 运行与验证

```powershell
py -3.12 latency_desk.py
py -3.12 -m unittest -v test_diagnostics test_monitor
```

测试在临时目录隔离配置，并使用模拟远端响应，不会调用模型服务；本机 ICMP/TCP 回环和 GUI 测试需 Windows 桌面会话。

## 打包

```powershell
powershell -NoProfile -File .\build.ps1
```

构建脚本创建当前目录的 `.venv`，首次下载 PyInstaller，先运行测试，再生成 `dist/网络延时助手.exe`。
若当前电脑的 PowerShell 策略不允许运行脚本，可在已激活 Python 环境中手动执行：

```powershell
python -m pip install -r requirements-build.txt
python -m unittest -v test_diagnostics test_monitor
python -m PyInstaller --onefile --windowed --name 网络延时助手 --icon latency.ico --add-data "latency.ico;." --version-file version_info.txt latency_desk.py
```

程序图标随 onefile 资源嵌入。不需要安装 Python 即可使用构建结果。分发时一并保留上一级“开源许可”中的运行环境许可。

## 数据与隐私

当前用户的 `%LOCALAPPDATA%/LatencyDesk/` 存储配置、两小时曲线和事件分类。源码/分发包不包含开发者自己的配置或历史。
Codex 日志只读：先扫描末尾最多 20,000 行，后续按自增 ID 增量读取；只提取连接相关 WARN/ERROR 的时间与分类。
不读取认证文件，不向日志数据库写入，不保存原始对话/日志文本。诊断导出包含手动备注、目标域名及网关/网卡等本机信息。

## 测量边界

HTTPS HEAD 使用 `http.client` 显式建立新连接，直连不解析 HTTP 代理环境设置；代理路线使用 HTTP CONNECT 隧道。
保留服务器返回的 HTTP 状态，不跟随重定向；403/429 等拒绝响应视作传输可达，但标为 HTTP 错误。
建连阶段合计 DNS/TCP/TLS/CONNECT，响应头阶段从请求发出计时。socket timeout 是每次阻塞操作的限制，不是整次探测的硬性总时限。
本工具不模拟需要认证的 WebSocket/SSE 模型流；相关故障通过实际 Codex 日志与采样时间关联观察。
网关 ICMP 被禁止时不能判断故障；多网卡时自动网关选择需要用户核实。TUN/TAP/VPN 可能接管直连。
自动诊断文案只是线索，不能据此确定故障责任。后台网络探测不保证与 Codex 使用完全相同的路由、进程代理设置和远端连接。

## 验证范围

已针对独立目标路由、HTTP 拒绝分类、传输失败、代理环境忽略、日志增量读取与脱敏、两小时清理、目标切换、网关选择、图表/事件页重开和 ZIP 导出编写测试。
Windows EXE 的启动、双击唤回、图表显示和退出保存另做人工/进程级检查。未在所有 Windows 版本或不同安全软件环境下验证。
