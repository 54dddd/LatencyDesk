# 网络延时助手 · LatencyDesk

Windows 桌面网络状态悬浮窗，同时观察直连与 Clash 代理的 HTTPS 请求耗时，并通过趋势图、分层探测和 Codex 连接事件辅助排查网络波动。

**当前版本：v2.0.1** · Windows 10/11 x64 · Python 3.12 + Tkinter · 运行时仅使用 Python 标准库

[下载 Windows 版](https://github.com/54dddd/LatencyDesk/releases/latest) · [版本改动](CHANGELOG.md) · [构建与测试](DEVELOPMENT.md) · [发布记录](RELEASES.md)

## 能做什么

- **双路悬浮监控**：显示直连和 Clash 代理请求耗时、HTTP 状态与短时曲线；两路独立调度，慢代理请求不拖住直连。
- **桌面常驻**：窗口可拖动、置顶、调整透明度；支持桌面快捷方式和再次启动时唤回窗口。
- **动态趋势**：浮窗显示本次启动后最近 5 分钟；详情可选 5 分钟、30 分钟、2 小时。趋势线展示中位值，范围线保留峰值，失败与无采样区间单独表示。
- **分层排障**：观察本机网关 ICMP、Clash 本地端口 TCP、国内目标与对照、代理目标与境外对照。
- **事件对照**：可选只读监听本机 Codex 日志中的连接错误；手动标记卡顿、切换节点、更换网络，将事件与曲线对照。
- **本地保存与导出**：保留最近两小时采样，并可导出诊断 ZIP，方便比较故障发生时间。

## 下载与启动

1. 在 [Releases](https://github.com/54dddd/LatencyDesk/releases) 下载 v2.0.1 Windows x64 压缩包。
2. **先完整解压**，再双击 `网络延时助手.exe`；使用 EXE 无需安装 Python。
3. 如需桌面图标，双击包内的 `创建桌面快捷方式.vbs`。
4. 点击浮窗齿轮，确认自己的 Clash **HTTP/Mixed 代理端口**，并设置测试地址、间隔、透明度等。
5. 点击「展开趋势」查看访问曲线、分层网络对照及 Codex 事件。

程序尝试发现系统代理及常见本地端口；自动发现结果仍需核对。默认直连目标为 `https://www.baidu.com/`，代理目标为 `https://chatgpt.com/`，国内对照为 `https://www.qq.com/`，境外对照为 `https://www.gstatic.com/generate_204`。可以在设置中修改。

## 怎么读数值

显示的是 **HTTPS HEAD 请求耗时，不是 ICMP ping，也不是模型生成回答的速度**。

- 建连计时包括 DNS、TCP、TLS，以及代理路线的 CONNECT；响应头阶段另行计时。
- 收到 HTTP 403、429 等表示收到服务器响应，但服务拒绝了请求；不能据低耗时认定服务正常。
- Clash 规则模式可能把某个目标分流为直连；TUN/TAP/VPN 也可能接管标记为直连的流量。
- 百度与 ChatGPT 属于不同目标。两路差异只能提供排查线索，不能直接证明故障在 Clash 或运营商。
- 短请求正常不代表 WebSocket/SSE 长连接稳定；本工具不模拟带认证的模型响应流，也不调用模型 API。
- 建连阶段每次阻塞等待上限 15 秒、响应头读取等待 8 秒，**不是整次请求的硬性总超时**；DNS 等仍可能等待更久。守护工作线程避免这些任务阻止应用退出。

建议一次只改变一个条件：先保持网络不变切换节点，再保持节点不变切换网络，结合曲线与实际断流事件观察。

## 从源码运行

安装 Python 3.12 x64，并包含 Tcl/Tk：

```powershell
git clone https://github.com/54dddd/LatencyDesk.git
cd LatencyDesk
py -3.12 latency_desk.py
```

运行不需要安装第三方 Python 包。打包需要 PyInstaller，具体命令见 [DEVELOPMENT.md](DEVELOPMENT.md)。

## 项目结构

| 文件 | 用途 |
| --- | --- |
| `latency_desk.py` | 浮窗、设置、趋势与事件界面、配置和历史 |
| `diagnostics.py` | 分层探测、网关发现、Codex 日志事件与诊断导出 |
| `probe_workers.py` | 固定并发的守护线程工作池 |
| `latency_plot.py` | 按时间分桶绘图，保留波动、失败与采样空档 |
| `test_diagnostics.py` / `test_monitor.py` | 探测、日志、绘图、保存和退出行为测试 |
| `build.ps1` / `requirements-build.txt` / `version_info.txt` | Windows EXE 构建资源 |
| `third_party_licenses/` | 分发包使用的 Python、Tk、PyInstaller 许可文本 |

## 本地数据与隐私

配置和采样保存在 `%LOCALAPPDATA%\LatencyDesk\`，包括 `settings.json`、`history.json`、`diagnostics.json`。仓库与发布包不包含开发者个人的这些文件。

Codex 日志监听可在设置中关闭：只读 SQLite，提取连接相关 WARN/ERROR 的时间和分类，不读取认证文件，不保存原始对话或日志正文。诊断导出可能包含目标域名、网关/网卡信息及手动备注，分享前请检查内容。

## 兼容性与已知限制

- 开发与本机验证环境为 Windows、Python 3.12；不支持将当前源码直接作为 macOS/Linux 应用运行。
- Tk 8.6 使用启动时的系统 DPI；移到不同缩放比例显示器后，必要时在目标显示器重启。
- 网关禁止 ICMP 时，对应结果不能直接解释成网络中断；多网卡场景需要核对自动选择的网关。
- Codex 日志格式或数据库位置改变后，事件监听可能需要调整。
- 自动诊断只提供线索；工具与其他应用的实际进程路由、代理设置和远端连接可能不同。

## 版本与许可

[CHANGELOG.md](CHANGELOG.md) 说明 v1.0、v1.1、v2.0、v2.0.1 的变化；旧版安装包作为历史发行保留，建议使用最新版本。v1.x 未保留完整源码快照，因此不会为它们伪造源码提交或源码标签。

项目自身暂未指定开源许可证；公开代码不等于授予任意使用、修改和再分发许可。第三方组件许可单独保存在 `third_party_licenses/`。
