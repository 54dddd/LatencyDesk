# 开发、构建与验证

## 环境

Windows 10/11 x64，Python 3.12 x64（含 Tcl/Tk）。运行代码只用标准库，PyInstaller 仅用于构建 EXE。

```powershell
py -3.12 latency_desk.py
py -3.12 -m unittest -v test_diagnostics test_monitor
```

测试使用临时目录与模拟远端响应，不调用模型 API。ICMP/TCP 回环与 Tkinter 测试需要 Windows 桌面会话。测试窗口可能短暂出现，测试不会修改已安装应用的配置。

## 构建 EXE

```powershell
powershell -NoProfile -File .\build.ps1
```

脚本在项目目录创建 `.venv`，按 `requirements-build.txt` 安装 PyInstaller，先运行测试，再输出 `dist/网络延时助手.exe`。首次安装依赖需要联网。

手动构建：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest -v test_diagnostics test_monitor
.\.venv\Scripts\python.exe -m PyInstaller --onefile --windowed --name 网络延时助手 --icon latency.ico --add-data "latency.ico;." --version-file version_info.txt latency_desk.py
```

## 版本维护

1. 更新源码与相关测试。
2. 同步 `version_info.txt`、README 中的当前版本以及 `CHANGELOG.md`。
3. 运行上述测试，并人工检查浮窗、设置、详情重开、再次启动唤回和正常退出。
4. 打包 EXE，随包附源码、使用说明及第三方许可；排除 `.venv`、缓存、用户设置、历史和诊断数据。
5. 创建对应 Git 标签，在 GitHub Releases 上传 Windows ZIP 和 SHA-256 校验值，写明相对上一版的变化。

只为可验证的源码快照建立源码版本标签。旧安装包没有源码时，记录其来源与限制，不用其他版本源码替代。

## 文件职责

`latency_desk.py` 管界面与两路主监控，`diagnostics.py` 管分层探测及事件记录，`probe_workers.py` 管并发与退出，`latency_plot.py` 管曲线分桶与绘制。

源码原有的 `README-源码.md` 和 `smoke_exe.py` 随快照保留；冒烟脚本含本机验证约定，日常验证优先使用上面的 unittest 命令。
