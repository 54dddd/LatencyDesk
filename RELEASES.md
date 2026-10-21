# 发行包与历史来源

当前推荐版本为 **v2.0.1**。完整变化见 [CHANGELOG.md](CHANGELOG.md)，Windows 安装包见 [GitHub Releases](https://github.com/54dddd/LatencyDesk/releases)。

| 版本 | 原始完成日期（北京时间） | 留存资料 |
| --- | --- | --- |
| v1.0 | 2026-10-20 | 原始 Windows x64 安装包、开发记录；未保留完整源码快照 |
| v1.1 | 2026-10-20 | 原始 Windows x64 安装包、开发记录；未保留完整源码快照 |
| v2.0 | 2026-10-20 | 原始 Windows x64 包，包含源码与测试 |
| v2.0.1 | 2026-10-21 | 已安装的 Windows x64 发行目录，包含源码与测试；此次重新压缩，不改动包内程序 |

v1.0、v1.1、v2.0 的 ZIP 仅改用便于下载的英文文件名，字节保持原样。v2.0.1 从已分发目录重新压缩，排除 Python 缓存。所有包保留第三方许可。

Git 提交是此次导入可验证源码快照的时间，不伪装成历史开发提交；v1.x 无源码快照，不建立会误导读者的源码版本标签。

下载后可用 PowerShell 检查 SHA-256，与发行页中的 `SHA256SUMS.txt` 对比：

```powershell
Get-FileHash .\LatencyDesk-v2.0.1-Windows-x64.zip -Algorithm SHA256
```

## 本次归档的 SHA-256

```text
961ae5355397ec48e79e90d2f69f1a963107662837de6c4e4148e8e8db984fc8  LatencyDesk-v1.0-Windows-x64.zip
a051e6fd63a9e923d5836254a8b2f51e9c60ec9f2482686c440df141ae72a26d  LatencyDesk-v1.1-Windows-x64.zip
5fa8cabb44a1812b80e3aa446f41235307caa4464b8355ce6227046e42cb390d  LatencyDesk-v2.0-Windows-x64.zip
c7a89e1f729a43f2bdd4cd1f3136fa8a169e5d4fce1dcbbcd890f4ddb81daf1e  LatencyDesk-v2.0.1-Windows-x64.zip
```
