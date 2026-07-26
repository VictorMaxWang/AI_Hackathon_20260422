# Windows Local Smoke Test

本页用于 Windows PowerShell 本地启动验证。目标是确认项目使用标准 Windows CPython 3.11+，而不是 MSYS2 Python。

Linux / macOS 请看 [`local_smoke_test_linux.md`](local_smoke_test_linux.md)。

## 1. 获取代码并检查解释器

```powershell
git clone https://github.com/VictorMaxWang/AI_Hackathon_20260422.git
cd AI_Hackathon_20260422

where.exe python
py -0p
```

如果 `python` 指向 `C:\msys64\...`，不要继续用默认 `python` 创建或运行项目环境。请使用 `py -3.11`。

## 2. 创建 `.venv`

如果 `.venv` 曾经用错误解释器创建，删除后重建：

```powershell
Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 3. 安装依赖

只用这一条命令，不要手工列依赖名。运行时依赖、测试依赖和包本身都由 `pyproject.toml` 声明：

```powershell
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

`[test]` extra 会额外装 `pytest` 和 `httpx`。`httpx` 是必须的：四个测试模块使用 `fastapi.testclient.TestClient`，而 `starlette.testclient` 需要 `httpx`（或 `httpx2`）才能导入。裸装 `fastapi` 不会带上它——`httpx` 只出现在 FastAPI 的 `standard` extra 里。

如果遇到 PyPI SSL 或网络问题，可以使用国内镜像：

```powershell
python -m pip install `
  -i https://pypi.tuna.tsinghua.edu.cn/simple `
  --trusted-host pypi.tuna.tsinghua.edu.cn `
  -e ".[test]"
```

验证：

```powershell
python -c "import fastapi, uvicorn, pydantic, paramiko, httpx; print('ok')"
```

预期输出：

```text
ok
```

## 4. 测试 smoke

```powershell
python -m pytest -q
```

测试不调用真实 LLM，也不创建或删除真实系统用户。

## 5. CLI smoke

```powershell
py -3.11 -m app.cli "帮我查看当前磁盘使用情况"
```

安装后也可以直接用控制台入口：

```powershell
guardedops "帮我查看当前磁盘使用情况"
```

预期输出应包含磁盘使用情况摘要。Windows 本地模式缺少部分类 Unix 工具（`getent`、`useradd`、`ss` 等），只读诊断会走 Windows fallback，写操作无法真实执行。完整运维验证需要 Linux 目标环境。

## 6. Web/API smoke

如果 8000 不可用，使用 8001：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

另开一个 PowerShell 窗口测试 API：

```powershell
$body = @{
  raw_user_input = "帮我查看当前磁盘使用情况"
} | ConvertTo-Json -Compress

$utf8Body = [System.Text.Encoding]::UTF8.GetBytes($body)

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/api/chat" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $utf8Body
```

或运行仓库内的 API smoke 脚本：

```powershell
.\scripts\smoke_api_windows.ps1 -Port 8001
.\scripts\smoke_api_windows.ps1 -Port 8001 -Message "帮我查看当前磁盘使用情况"
```

`scripts/start_web_windows.ps1` 和 `scripts/run_cli_windows.ps1` 假设 `.venv` 存在于仓库根目录，会自动切到仓库根再启动。

## 7. 常见问题

- `python` 指向 `C:\msys64\...`：改用 `py -3.11`，并删除后重建 `.venv`。
- `ModuleNotFoundError: No module named 'httpx'`：漏装了 `[test]` extra，重跑第 3 节的安装命令。
- 依赖 import 失败：确认已激活 `.venv`，并重新执行依赖安装命令。
- 8000 端口绑定失败：使用 8001。
- PowerShell 执行脚本受限：可直接运行 README 中的手工命令，或 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`。
- 写操作报找不到 wrapper 脚本：Windows 本地模式不支持真实用户创建/删除，见 [`sudo_wrapper_deployment.md`](sudo_wrapper_deployment.md)。
