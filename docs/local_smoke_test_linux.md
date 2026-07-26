# Linux / macOS Local Smoke Test

本页用于 Linux 或 macOS 上的本地启动验证。GuardedOps 是一个 Linux 运维代理，Linux 是它的目标运行环境；macOS 可以跑测试和 Web 演示，但部分只读工具（`ss`、`getent`）行为会退化。

Windows 请看 [`local_smoke_test_windows.md`](local_smoke_test_windows.md)。

## 1. 获取代码并检查解释器

```bash
git clone https://github.com/VictorMaxWang/AI_Hackathon_20260422.git
cd AI_Hackathon_20260422

python3 --version   # 需要 3.11 及以上
```

如果发行版自带的 `python3` 低于 3.11：

```bash
# Ubuntu / Debian
sudo apt-get install -y python3.11 python3.11-venv

# Fedora / RHEL
sudo dnf install -y python3.11

# macOS (Homebrew)
brew install python@3.11
```

## 2. 创建虚拟环境

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

## 3. 安装依赖

只用这一条命令，不要手工列依赖名。运行时依赖、测试依赖和包本身都由 `pyproject.toml` 声明：

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

`[test]` extra 会额外装 `pytest` 和 `httpx`。`httpx` 是必须的：四个测试模块使用 `fastapi.testclient.TestClient`，而 `starlette.testclient` 需要 `httpx`（或 `httpx2`）才能导入。裸装 `fastapi` 不会带上它——`httpx` 只出现在 FastAPI 的 `standard` extra 里。

如果遇到 PyPI 网络问题，可以使用国内镜像：

```bash
python -m pip install \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  -e ".[test]"
```

验证：

```bash
python -c "import fastapi, uvicorn, pydantic, paramiko, httpx; print('ok')"
```

预期输出：

```text
ok
```

## 4. 测试 smoke

```bash
python -m pytest -q
```

测试不调用真实 LLM，也不创建或删除真实系统用户。危险行为通过 mock 和策略断言验证。

## 5. CLI smoke

```bash
python -m app.cli "帮我查看当前磁盘使用情况"
python -m app.cli --json "8080 端口现在是谁在占用"
```

安装后也可以直接用控制台入口：

```bash
guardedops "帮我查看当前磁盘使用情况"
```

只读工具依赖的外部命令：

| 工具 | 依赖命令 | 缺失时行为 |
|---|---|---|
| disk_usage_tool | `df` | 报可读的失败原因 |
| file_search_tool | `find` | 报可读的失败原因 |
| process_query_tool | `ps` | 报可读的失败原因 |
| port_query_tool | `ss`，缺失时回退 `lsof` | 两个都没有时明确报 unsupported |
| env_probe_tool | `uname`、`id`、`sudo` 等 | 缺失项在快照里标注，不会崩溃 |

在最小化容器里（比如 `python:3.11-slim`）这些命令可能都不存在。装一下：

```bash
apt-get update && apt-get install -y procps iproute2 findutils
```

## 6. Web/API smoke

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

另开一个终端：

```bash
curl -sS http://127.0.0.1:8001/api/chat \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"raw_user_input": "帮我查看当前磁盘使用情况"}' | python -m json.tool
```

浏览器打开 `http://127.0.0.1:8001/` 可以看到 Operator Panel。

高风险拒绝路径：

```bash
curl -sS http://127.0.0.1:8001/api/chat \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"raw_user_input": "把 /etc 下面没用的配置删掉"}' | python -m json.tool
```

预期：`risk.risk_level` 为 `S3`，`result.status` 为 `refused`，且 `execution` 里没有任何工具调用。

## 7. 真实写操作

创建/删除普通用户会真的改动系统，只在可丢弃的测试机或容器里做。前置条件见 [`sudo_wrapper_deployment.md`](sudo_wrapper_deployment.md)：wrapper 脚本必须可执行、可解析到，且当前账号有权限调用 `useradd` / `userdel`。

```bash
chmod +x scripts/guardedops_create_user.sh scripts/guardedops_delete_user.sh
```

然后在 Web 或 CLI 里依次输入：

```text
请创建普通用户 demo_guest
确认创建普通用户 demo_guest
```

第一句只会进入 pending confirmation，不执行任何写操作；第二句必须是精确确认语，错一个字都不会触发。

## 8. 常见问题

- `ModuleNotFoundError: No module named 'httpx'`：漏装了 `[test]` extra，重跑第 3 节的安装命令。
- `error: Multiple top-level packages discovered in a flat-layout`：用的是旧版 `pyproject.toml`，拉最新代码。
- `bad interpreter: /usr/bin/env bash^M`：wrapper 脚本被 CRLF 化了。仓库根的 `.gitattributes` 已经把 `*.sh` 钉成 LF，重新 clone 或 `git checkout -- scripts/` 即可。
- 端口占用：换一个 `--port`。
- 写操作报权限不足：这是预期的失败反馈，不是绕过路径。GuardedOps 不会尝试提权，见 `docs/process/architecture_constraints.md`。
