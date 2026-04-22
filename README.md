# GuardedOps

GuardedOps 是一个安全对话式 Linux/SSH 运维代理项目。

本项目用于 AI Hackathon 2026《操作系统智能代理》赛题，目标是在真实 Linux/SSH 运维场景中探索可运行、可验证、可审计的安全代理形态。

当前阶段处于仓库初始化阶段，仅建立基础目录、工程配置和后续开发入口，不代表核心能力已经完成。

核心安全边界：

- 禁止 arbitrary shell。
- 禁止 raw command mode。
- Prompt 不作为最终风控边界。
- 执行层只允许调用白名单工具。

## CLI 调试入口

当前 CLI 仅用于本地调试和 smoke test，会调用现有只读 Orchestrator，不是 raw shell，也不会把自然语言拼成 bash 执行。

示例：

```bash
python -m app.cli "帮我查看当前磁盘使用情况"
python -m app.cli "8080 端口现在是谁在占用"
python -m app.cli --json "帮我看当前 CPU 占用最高的 10 个进程"
```

当前 CLI 只覆盖 Phase 1 只读基础能力：磁盘使用、文件检索、进程查询和端口查询。未知请求或写操作会被拒绝。

## Web/API 启动

安装依赖后启动本地演示服务：

```bash
uvicorn app.main:app --reload
```

打开浏览器访问：

```text
http://127.0.0.1:8000/
```

## `/api/chat`

当前提供最小只读入口：

```http
POST /api/chat
Content-Type: application/json
```

请求体：

```json
{
  "raw_user_input": "帮我查看当前磁盘使用情况"
}
```

响应直接来自只读 orchestrator 的统一结构，包含 `intent`、`environment`、`risk`、`plan`、`execution`、`result` 和 `explanation`。

当前阶段只支持 Phase 1 只读基础能力，包括磁盘、文件检索、进程和端口查询。不支持任意命令执行，不支持 raw command mode，不支持写操作。

## Phase 1 测试与最小验证

运行全量测试：

```bash
pytest
```

最小 CLI smoke test：

```bash
python -m app.cli "帮我查看当前磁盘使用情况"
python -m app.cli --json "8080 端口现在是谁在占用"
```

最小 API smoke test：

```bash
uvicorn app.main:app --reload
```

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"raw_user_input":"帮我查看当前磁盘使用情况"}'
```

当前 Phase 1 测试覆盖：

- 核心 Pydantic 模型与审计 envelope 的序列化约束。
- LocalExecutor / SSHExecutor 的 argv-only 执行约束、超时、错误和输出截断。
- 环境探测 env_probe 的主机、发行版、用户、sudo、命令可用性和连接模式字段。
- 只读工具：磁盘、文件检索、进程查询、端口查询。
- 只读 orchestrator 的 parse / plan / env_probe / tool / summary 闭环。
- CLI 调试入口的文本输出、JSON 输出和写操作拒绝。
- Web/API `/api/chat` 的 TestClient 只读入口和静态页面资源。

当前仍未覆盖或未实现：

- 写操作能力，如创建用户、删除用户、修改系统配置。
- policy engine、确认机制、Session Memory 和真实 LLM 接入。
- 真实远程 SSH 环境集成测试。
- 持久化审计存储，如 SQLite / JSONL 查询闭环。

后续开发将遵循 `agent.md` 与 `architecture_constraints.md` 中定义的任务边界、安全约束和状态更新规则。

更多能力、使用方式、架构说明和演示材料将在后续阶段补充。
