# GuardedOps

GuardedOps 是一个安全对话式 Linux/SSH 运维代理项目。

本项目用于 AI Hackathon 2026《操作系统智能代理》赛题，目标是在真实 Linux/SSH 运维场景中探索可运行、可验证、可审计的安全代理形态。

当前 README 仍是阶段性说明，不是最终提交版。当前仓库已覆盖 Phase 1 只读基础能力、Phase 2 受限用户写操作的风控/确认/拒绝闭环测试，以及 Phase 3 多轮上下文与连续任务的核心测试。

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

当前提供统一对话入口：

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

当前接口支持 Phase 1 只读基础能力，以及 Phase 2 普通用户创建/删除的确认闭环。高风险写操作会被策略拒绝。不支持任意命令执行，不支持 raw command mode。

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

## Phase 2 测试与确认闭环验证

运行全量测试：

```bash
pytest
```

只运行 Phase 2 相关测试：

```bash
pytest tests/test_policy.py tests/test_validators.py tests/test_user_tools.py tests/test_confirmation.py tests/test_high_risk_refusal.py tests/test_api_confirmation.py
```

当前 Phase 2 测试覆盖：

- 风控引擎：S1/S2 确认、S3 高风险拒绝、受保护路径、sudoers、sshd_config、批量权限变更。
- 用户名校验：保留系统用户、注入字符、空白、通配符、非 ASCII 和长度边界。
- 用户管理工具：全 mock 验证创建/删除用户流程，不在真实系统创建或删除用户。
- 确认状态机：正确确认语执行，错误确认语不执行，取消确认清理 pending action。
- 高风险拒绝闭环：S3 请求不进入确认，不执行 env_probe 或任何工具。
- Web/API 展示：`/api/chat` 返回 pending confirmation、risk、plan、execution、result、safe_alternative，静态页面包含确认与拒绝展示区域。

## Phase 3 测试与连续任务验证

运行全量测试：

```bash
pytest
```

只运行 Phase 3 相关测试：

```bash
pytest tests/test_session_memory.py tests/test_multistep_planner.py tests/test_continuous_tasks.py tests/test_llm_parser_stub.py
```

当前 Phase 3 测试覆盖：

- Session Memory：记录最近用户名、路径、端口和风险等级；支持“刚才那个用户”等上下文解析；无上下文时拒绝猜测并跳过执行。
- 多步 Planner：生成结构化 `ExecutionPlan` / `PlanStep`，覆盖环境探测后创建普通用户、端口查询后查询对应进程、上下文删除用户和不支持复杂任务的拒绝。
- 连续任务 Orchestrator：覆盖暂停等待确认、确认后恢复、确认语不匹配保持 pending、取消 pending、前置失败中止后续步骤，以及创建/删除后的验证 timeline。
- timeline 输出：每个连续任务节点包含 `step_id`、`intent`、`risk`、`status` 和 `result_summary`，用于演示与审计材料。
- LLM parser stub：`app.agent.llm_parser` 当前保持禁用态，测试确认不会发起真实网络请求或依赖外部模型 API。

环境说明：本仓库当前可用的 `pytest` 命令使用已安装依赖的 Python 3.11 环境；如果 `python -m pytest` 指向缺少依赖的其他 Python，需要切换解释器或安装项目依赖后再运行。

当前仍未覆盖或未实现：

- 真实 LLM 接入尚未实现；当前只有禁用态 parser stub。
- 真实远程 SSH 环境集成测试。
- 持久化审计存储，如 SQLite / JSONL 查询闭环。
- 审计导出、最终交付文档、自测报告和演示材料整理。

后续开发将遵循 `agent.md` 与 `architecture_constraints.md` 中定义的任务边界、安全约束和状态更新规则。

更多能力、使用方式、架构说明和演示材料将在后续阶段补充。
