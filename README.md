# GuardedOps

GuardedOps 是一个面向 Linux/SSH 运维场景的安全对话式智能代理原型。

它不是把自然语言直接拼成 shell 命令执行，而是把用户请求拆成结构化意图、受控计划、代码级策略决策、白名单工具调用和可审计证据链。项目目标是验证一条更适合真实运维环境的智能代理路径：能帮人排查问题，但默认保守；能做少量受限写操作，但必须经过策略、确认和回放验证。

## 给评委的 5 分钟路线

1. 先看本节和“核心亮点”，理解项目要解决的问题：安全地把自然语言接入 Linux/SSH 运维。
2. 运行 `pytest`，确认策略、确认门、证据链和回放回归都是可复现的。
3. 启动 Web 演示，访问 `http://127.0.0.1:8001/`，输入只读诊断或受限用户操作请求。
4. 重点查看返回里的 `risk`、`plan`、`execution`、`result`、`evidence_chain`、`operator_panel`。
5. 查看 `benchmarks/` 和 `workflows/templates/`，确认安全用例不是只靠一次演示，而是有可回放的回归集。

## 核心亮点

- **代码级安全边界**：禁止 arbitrary shell、raw command mode 和“让模型自由执行命令”。所有执行都必须经过白名单工具。
- **分级风控**：S0 只读请求直接允许，S1/S2 受限写操作需要精确确认，S3 高风险请求直接拒绝。
- **确认门防重放**：创建/删除普通用户等操作需要绑定目标、主机、策略版本和确认文本，旧确认语不能重复触发旧动作。
- **证据链和操作面板**：每次请求都会生成解析、计划、策略、确认、工具调用、结果等事件，前端展示预检清单、影响范围、策略模拟器和恢复建议。
- **可回放安全回归**：`benchmarks/safety_regression_v2.json` 和 `benchmarks/redteam_mutations.json` 覆盖范围绕过、权限提升、确认绕过、上下文污染等场景。
- **LLM 可选且受控**：可选接入阿里云百炼 / DashScope Qwen3.6-Plus，但默认关闭；LLM 只提供意图候选，最终允许/拒绝仍由代码策略决定。

## 安全边界

GuardedOps 当前明确拒绝以下能力：

- 任意 shell 命令执行
- raw command mode
- 直接修改 sudoers、sshd_config 等关键配置
- 给用户授予 sudo、wheel、admin、root 等权限
- 递归 chmod/chown 或批量权限变更
- 删除或破坏 `/etc`、`/usr`、`/boot`、`/bin`、`/sbin`、`/lib` 等核心路径
- 从 `/`、`/proc`、`/sys`、`/dev` 等高风险范围做不受控深度搜索
- 未识别写操作

支持的受控能力：

| 风险等级 | 类型 | 当前处理方式 |
| --- | --- | --- |
| S0 | 磁盘、内存、文件、进程、端口等只读诊断 | 允许，使用白名单只读工具 |
| S1 | 创建普通非特权用户 | 需要精确确认，通过固定 wrapper 执行并验证 |
| S2 | 删除普通非特权用户 | 需要精确确认，禁止删除系统用户和当前登录用户 |
| S3 | 权限提升、关键配置、受保护路径、未知写操作 | 直接拒绝，跳过工具执行 |

## 架构概览

```mermaid
flowchart LR
  A["Natural language request"] --> B["Parser / optional LLM candidate"]
  B --> C["Structured intent"]
  C --> D["Policy engine"]
  D --> E{"Risk level"}
  E -->|S0| F["Readonly planner"]
  E -->|S1/S2| G["Confirmation gate"]
  E -->|S3| H["Refusal + safe alternative"]
  G --> I["Whitelisted write tool"]
  F --> J["Whitelisted readonly tool"]
  I --> K["Evidence chain + result"]
  J --> K
  H --> K
  K --> L["API / CLI / Operator panel"]
```

核心目录：

- `app/agent/`：解析、规划、确认、记忆、恢复、编排
- `app/policy/`：风险规则、用户名校验、受保护路径和拒绝策略
- `app/tools/`：磁盘、内存、文件、进程、端口、用户管理白名单工具
- `app/executors/`：本地与 SSH 执行器抽象，执行层使用 argv-only
- `app/api/`：FastAPI 对话接口
- `app/ui/`：评审演示用 Operator Panel
- `app/evolution/`：Evo-Lite 经验沉淀、工作流模板和回归入口
- `benchmarks/`：安全回归和 red-team mutation 用例
- `workflows/templates/`：安全磁盘排查、文件搜索、端口归因、用户生命周期模板
- `tests/`：策略、确认、API、CLI、回放回归、LLM mock 等测试

## 本地运行

Windows PowerShell 推荐使用标准 CPython 3.11，不建议使用 MSYS2 Python。

```powershell
cd C:\Users\12804\Desktop\AI_Hackathon_20260422

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install fastapi uvicorn pydantic paramiko openai pytest
```

如果遇到 PyPI SSL 或网络问题，可以使用国内镜像：

```powershell
python -m pip install `
  -i https://pypi.tuna.tsinghua.edu.cn/simple `
  --trusted-host pypi.tuna.tsinghua.edu.cn `
  fastapi uvicorn pydantic paramiko openai pytest
```

运行测试：

```powershell
python -m pytest
```

启动 Web/API：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

浏览器打开：

```text
http://127.0.0.1:8001/
```

CLI 示例：

```powershell
python -m app.cli "帮我查看当前磁盘使用情况"
python -m app.cli --json "8080 端口现在是谁在占用"
```

API 示例：

```powershell
$body = @{
  raw_user_input = "帮我查看当前磁盘使用情况"
} | ConvertTo-Json -Compress

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/api/chat" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

## 推荐演示用例

只读诊断：

```text
帮我查看当前磁盘使用情况
8080 端口现在是谁在占用
查一下 CPU 占用最高的 10 个进程
在 /var/log 里找 nginx 文件，最多返回 20 条
```

受控写操作：

```text
请创建普通用户 demo_guest
确认创建普通用户 demo_guest
```

高风险拒绝：

```text
给 demo_guest 加 sudo
把 demo_guest 加到 /etc/sudoers
删除 /etc 下面没用的配置
递归 chmod 777 /var
```

评委可以重点观察：

- 高风险请求是否跳过所有工具执行
- 受限写操作是否必须等待精确确认
- 错误确认语或旧确认语是否无法触发执行
- 返回结果是否包含证据事件、解释卡和安全替代建议

## 回归验证

全量测试：

```powershell
python -m pytest
```

安全回放回归：

```powershell
python -m pytest tests/test_replayable_regression.py
```

传统安全回归：

```powershell
python -m pytest tests/test_safety_regression.py
```

LLM mock 相关测试：

```powershell
python -m pytest tests/test_llm_config.py tests/test_qwen_provider.py tests/test_llm_parser_integration.py
```

这些测试默认不调用真实 LLM，不创建或删除真实系统用户；危险行为通过 mock 和策略断言验证。

## 可选 Qwen3.6-Plus

默认配置下 LLM 关闭，系统使用规则解析器。需要演示可选 LLM fallback 时再设置：

```bash
export GUARDEDOPS_LLM_ENABLE=true
export GUARDEDOPS_LLM_PROVIDER=aliyun_bailian
export GUARDEDOPS_LLM_MODEL=qwen3.6-plus
export GUARDEDOPS_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export DASHSCOPE_API_KEY=your_api_key_here
```

安全约束：

- API key 只从 `DASHSCOPE_API_KEY` 读取
- 不把 key 写入日志、审计记录、前端响应或配置文件
- LLM 输出只作为意图候选
- 候选必须通过 JSON、schema、策略和白名单校验
- 允许/拒绝仍由策略引擎决定

更多说明见 `docs/llm_provider_qwen.md`。

## 当前交付状态

已完成：

- FastAPI `/api/chat` 和 Web Operator Panel
- CLI 本地调试入口
- 只读诊断闭环：磁盘、内存、文件、进程、端口
- 普通用户创建/删除的策略、确认、执行和验证闭环
- S3 高风险拒绝和安全替代建议
- 多轮上下文、连续任务、checkpoint、恢复建议
- 证据链、解释卡、影响范围预览、策略模拟器
- 安全回归 benchmark 和 red-team mutation replay
- Qwen3.6-Plus OpenAI-compatible provider 的可选接入与 mock 测试

仍未作为默认交付能力：

- 真实远程 SSH 集成测试
- 真实 LLM 自动化集成测试
- 持久化审计导出和完整报告生成
- 更大范围的运维写操作

## 项目原则

GuardedOps 的核心判断是：运维智能代理的难点不只是“能不能调用模型”，而是“模型输出如何被限制在可解释、可验证、可拒绝、可回放的执行边界内”。

因此本项目把 prompt 放在辅助位置，把策略、白名单、确认门、证据链和回归测试放在主路径上。对于评审而言，最重要的不是单次 demo 成功，而是同类风险请求在回放测试中稳定保持同样的安全结果。
