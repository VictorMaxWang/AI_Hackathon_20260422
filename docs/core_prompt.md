# GuardedOps Core Prompt

这是 GuardedOps 实际发货的 LLM 系统提示词，以及它的输出契约。

**唯一事实来源是 `app/llm/prompts.py::INTENT_CANDIDATE_SYSTEM_PROMPT`。** 本文档第 2 节逐字引用它，而不是转述，这样文档和代码不会各自漂移。第 3 节说明的是 `app/agent/llm_parser.py` 里代码级校验的行为——那才是真正的边界，提示词只是第一道礼貌性提醒。

## 1. LLM 的角色

LLM 在 GuardedOps 里**已经启用，但默认关闭**（`GUARDEDOPS_LLM_ENABLE` 默认 false）。启用后它只做一件事：在规则解析器返回 `unknown` 时，提供一个**意图候选**。

它可以：

- 理解用户的运维请求；
- 产出一个结构化意图候选；
- 给出一句简短解释（`explanation`）。

它不可以：

- 决定 allow / deny；
- 决定最终风险等级；
- 输出 shell 命令、argv、脚本或工具名；
- 绕过确认门。

> Prompt 不是安全边界。最终安全边界由代码实现：validators、policy engine、risk engine、confirmation policy、protected path rules、scope limiter 和白名单执行层。

### 1.1 三条硬性禁止项

这三条是代码强制的，不是提示词里的请求。每一条都在第 3 节有对应的校验实现：

- **不得直接生成 bash**——命令样文本（`rm -`、`bash -c`、`useradd` 等）在候选的任意字符串值里出现即整体拒绝，见 3.3。
- **不得绕过 policy engine**——候选构造完成后还要再跑一遍 policy 并断言结论一致，见 3.6；`allow`、`decision`、`override_policy` 等字段名在任意层级出现即整体拒绝，见 3.2。
- **不得直接驱动执行层**——LLM 只能产出意图候选，工具名与 argv 由白名单执行层自行决定，模型输出里的 `command`、`argv`、`tool_name` 一律是拒绝理由。

如果 LLM 被禁用、配置错误、不可用，或者返回了无法通过校验的输出，系统回落到规则解析器。这条回落路径在 `app/agent/llm_parser.py::parse_with_llm` 里，返回 `status: "fallback"` 和空候选列表。

### 关于「不依赖网络模型」

这份文档的早期版本写着「不得新增网络模型 fallback 或 API key 依赖」。那条约束**已经不成立**：项目确实接了阿里云百炼 / DashScope 的 Qwen3.6-Plus，通过 OpenAI 兼容接口调用，API key 从 `DASHSCOPE_API_KEY` 读取。

改变的是能力，不是边界：LLM 从来不参与 allow/deny，默认关闭，不装 `[llm]` extra 也能完整运行。相关决策见 `process/decision_log.md` 的 DEC-INIT-03 / DEC-INIT-04，provider 细节见 `llm_provider_qwen.md`。

## 2. 发货的系统提示词（逐字引用 `app/llm/prompts.py`）

```text
You are GuardedOps intent-candidate assistant.

You must output exactly one JSON object and nothing else.
You must not output shell commands, bash commands, raw commands, argv, scripts, or tool names.
You must not decide allow or deny.
You must not override policy.
You must not bypass confirmation.
You only generate a candidate intent for later code validation.
If unsupported or uncertain, return intent "unsupported".
For high-risk requests, set intent "high_risk_request" and risk_hint, but do not decide final risk.

risk_hint must stay null for every normal request. Any non-empty risk_hint makes the
validator downgrade the candidate to an unknown write operation, which the policy
engine then refuses.

create_user and delete_user are recognized but disabled by default. Unless the
operator explicitly enabled write candidates, the validator downgrades them to an
unknown write operation, which the policy engine then refuses.

Every path in target.path and target.base_paths must be an absolute, already
normalized POSIX path: it must start with "/", must not start with "-", and must
contain no "..", no "//", and no trailing slash. Any other value is rejected.

Allowed intent values:
disk_usage, memory_usage, file_search, process_query, port_query, create_user, delete_user, high_risk_request, unsupported

Output JSON shape:
{
  "intent": "disk_usage | memory_usage | file_search | process_query | port_query | create_user | delete_user | high_risk_request | unsupported",
  "target": {
    "username": null,
    "path": null,
    "port": null,
    "pid": null,
    "keyword": null,
    "base_paths": []
  },
  "constraints": {},
  "context_refs": [],
  "requires_write": false,
  "risk_hint": null,
  "confidence": 0.0,
  "explanation": "brief reason"
}
```

### User message

`build_intent_candidate_messages()` 把用户输入包成一条 JSON user message，而不是直接拼进 system prompt：

```json
{
  "raw_user_input": "<截断到 2000 字符>",
  "context": {
    "last_username": null,
    "last_path": null,
    "last_port": null,
    "last_pid": null,
    "last_intent": null,
    "last_risk_level": null,
    "session_id": null
  }
}
```

`context` 是白名单过滤的：只有上面这七个键会被传出去，字符串值截断到 200 字符。会话里的其他任何东西——工具输出、命令结果、环境快照——都不会进入提示词。

## 3. 代码级校验（真正的边界）

模型输出到达 policy engine 之前，`app/agent/llm_parser.py::_validated_candidate` 会做下面这些事。**任何一条不满足，整个候选被丢弃，系统回落到规则解析器**，而不是"尽力修复后放行"。

### 3.1 结构

- 输出必须能解析成 JSON 对象。允许剥掉 ```` ```json ```` 围栏；如果顶层是数组或 `{"candidates": [...]}`，取第一个元素。
- 最终必须通过 `ParsedIntent` 的 Pydantic schema 校验。

### 3.2 禁止字段（递归检查所有层级的键名）

出现下列任一键名，直接拒绝：

```text
allow, argv, bash, cmd, command, commands, confirmation_bypass, decision, deny,
execute, execution_plan, final_decision, override_policy, policy_override,
raw_command, raw_shell, script, shell, skip_confirmation, tool, tool_name
```

### 3.3 禁止命令样文本（递归检查所有字符串值）

任何字符串值只要匹配下列模式就整体拒绝：`rm -`、`chmod [0-7]`、`chown`、`useradd`、`userdel`、`bash -c`、`sh -c`、`powershell -command`、`cmd /c`、`run_shell_tool`、`execute_command_tool`、`bash_tool`、`raw shell`、`&&`、`||`、反引号、`$(`。

### 3.4 意图映射（只认这些，其余一律拒绝）

| LLM 输出的 intent | 映射到的规范意图 | 说明 |
|---|---|---|
| `disk_usage` | `query_disk_usage` | 只读 |
| `memory_usage` | `query_memory_usage` | 只读 |
| `file_search` | `search_files` | 只读，必须有范围限制 |
| `process_query` | `query_process` | 只读 |
| `port_query` | `query_port` | 只读 |
| `create_user` | `create_user` | 写；**默认降级**为 unknown write |
| `delete_user` | `delete_user` | 写；**默认降级**为 unknown write |
| `high_risk_request` | `unknown`（requires_write=true） | 由 policy engine 拒绝 |
| `unsupported` | 无候选，回落规则解析器 | |

只读意图如果自己把 `requires_write` 标成 true，直接拒绝。

### 3.5 只能收紧，不能放宽

这是整个设计里最关键的一条：**LLM 候选永远不能让结果比规则解析器更宽松。**

- **写意图默认禁用。** `create_user` / `delete_user` 只有在 `GUARDEDOPS_LLM_ALLOW_WRITE_INTENTS` 显式开启时才保留原意图；否则降级成未知写操作，被 policy engine 拒绝。
- **任何非空 `risk_hint` 都会触发降级。** 模型说"这有点危险"不会变成一次温和的处理，而是直接变成未知写操作。
- **原始请求里的提权信号会覆盖模型判断。** 请求文本里出现 sudo / root / 管理员 / 提权 / 最高权限等信号（中英文都匹配）时，任何非只读候选都被强制降级为未知写操作，并打上 `danger_category: privilege_escalation`。
- **路径必须已经是绝对且规范化的。** 必须以 `/` 开头，不能以 `-` 开头，不能含 `..`、`//` 或尾部斜杠，不能含控制字符，长度 ≤ 512。规范化由 `app/policy/rules.py::normalize_path` 判定——注意这里是**拒绝**不规范路径，而不是替模型把它规范化后放行。
- **候选必须能映射到白名单工具。** 只读意图如果映射不到 `INTENT_TOOL_WHITELIST` 里的工具，拒绝。

### 3.6 出口再校验一次 policy

候选构造完成后，`_validate_policy_and_tool_boundary` 会**先跑一遍 policy engine**，然后断言：

- 允许的写操作必须要求确认；
- 任何高于 S0 的允许结果必须要求确认；
- S0 允许的意图必须落在工具白名单里。

任何一条不成立，候选被丢弃。也就是说，即使前面所有过滤都被绕过，一个"允许且不需要确认的写操作"也无法作为 LLM 候选存活。

### 3.7 体量上限

`base_paths` ≤ 8 项，`constraints` ≤ 24 个键、每个字符串值 ≤ 240 字符，`context_refs` ≤ 8 项每项 ≤ 120 字符，`username` / `keyword` ≤ 64 字符，`explanation` ≤ 240 字符，`risk_hint` ≤ 120 字符，`confidence` 钳制到 `[0, 1]`（NaN 视为 0）。

## 4. 溯源

| 内容 | 代码位置 |
|---|---|
| 系统提示词文本 | `app/llm/prompts.py::INTENT_CANDIDATE_SYSTEM_PROMPT` |
| 消息组装与 context 白名单 | `app/llm/prompts.py::build_intent_candidate_messages` |
| 输出校验与降级规则 | `app/agent/llm_parser.py::_validated_candidate` |
| 出口 policy 复核 | `app/agent/llm_parser.py::_validate_policy_and_tool_boundary` |
| 开关与配置 | `app/config.py::load_config` |
| provider 实现 | `app/llm/qwen_provider.py` |

对应测试：`tests/test_llm_parser_stub.py`、`tests/test_llm_parser_integration.py`、`tests/test_llm_config.py`、`tests/test_qwen_provider.py`。这些测试全部使用注入的 mock provider，不会调用真实 DashScope。

## 5. 修改这份文档的规则

第 2 节是从 `app/llm/prompts.py` 逐字复制的。改了提示词就必须同步改这里；改了这里但没改代码，等于文档说谎。第 3 节描述的是 `app/agent/llm_parser.py` 的实际行为，改校验逻辑时同样要回来更新。

`process/architecture_constraints.md` 第 3 节把「核心 Prompt 文本」列为必交付文档，指的就是这一份。
