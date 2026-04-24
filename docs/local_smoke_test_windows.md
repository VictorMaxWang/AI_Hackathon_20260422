# Windows Local Smoke Test

本页用于 Windows PowerShell 本地启动验证。目标是确认项目使用标准 Windows CPython 3.11，而不是 MSYS2 Python。

## 1. 检查解释器

```powershell
cd C:\Users\12804\Desktop\AI_Hackathon_20260422

where python
py -0p
```

如果 `python` 指向 `C:\msys64\...`，不要继续用默认 `python` 创建或运行项目环境。请使用 `py -3.11`。

## 2. 重建 `.venv`

如果 `.venv` 曾经用错误解释器创建，删除后重建：

```powershell
Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 3. 安装并验证依赖

```powershell
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn fastapi uvicorn pydantic paramiko openai pytest

python -c "import fastapi, uvicorn, pydantic; print('ok')"
```

预期输出包含：

```text
ok
```

## 4. CLI smoke

```powershell
py -3.11 -m app.cli "帮我查看当前磁盘使用情况"
```

预期输出应包含磁盘使用情况摘要。Windows 本地模式可能缺少部分类 Unix 工具，完整运维测试建议使用 Linux/SSH 目标环境。

## 5. Web/API smoke

如果 8000 不可用，使用 8001：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

另开一个 PowerShell 窗口测试 API：

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/api/chat" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"raw_user_input":"帮我查看当前磁盘使用情况"}'
```

## 6. 常见问题

- `python` 指向 `C:\msys64\...`：改用 `py -3.11`，并删除后重建 `.venv`。
- 依赖 import 失败：确认已激活 `.venv`，并重新执行依赖安装命令。
- 8000 端口绑定失败：使用 8001。
- PowerShell 执行脚本受限：可直接运行 README 中的手工命令。
