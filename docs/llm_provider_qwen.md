# Qwen3.6-Plus LLM Provider

GuardedOps can optionally use Alibaba Cloud Bailian / DashScope Qwen3.6-Plus through the OpenAI-compatible API. The provider is disabled by default and is only used as a fallback intent-candidate parser when the rule-based parser returns `unknown`.

## Enable Qwen3.6-Plus

```bash
export GUARDEDOPS_LLM_ENABLE=true
export GUARDEDOPS_LLM_PROVIDER=aliyun_bailian
export GUARDEDOPS_LLM_MODEL=qwen3.6-plus
export GUARDEDOPS_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export DASHSCOPE_API_KEY=your_api_key_here
```

Supported base URLs:

```bash
# Beijing
export GUARDEDOPS_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# Singapore
export GUARDEDOPS_LLM_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1

# US Virginia
export GUARDEDOPS_LLM_BASE_URL=https://dashscope-us.aliyuncs.com/compatible-mode/v1
```

Optional tuning:

```bash
export GUARDEDOPS_LLM_TIMEOUT_SECONDS=30
export GUARDEDOPS_LLM_MAX_TOKENS=1024
export GUARDEDOPS_LLM_TEMPERATURE=0
```

## Safety Boundary

- API keys are read only from `DASHSCOPE_API_KEY`.
- API keys must not be hardcoded, logged, audited, shown in frontend responses, or committed in config files.
- LLM output is only an intent candidate.
- LLM output must be JSON and must pass `ParsedIntent` schema validation.
- LLM candidates still go through the existing policy engine, confirmation state machine, planner, whitelist tools, executor, and evidence layer.
- LLM cannot decide final allow or deny.
- LLM cannot output shell commands, raw commands, bash scripts, tool names, policy overrides, or confirmation bypasses.
- If LLM is disabled, misconfigured, unavailable, or returns invalid output, GuardedOps falls back to the existing rule-based parser.

## Smoke Tests

Without LLM:

```bash
python -m app.cli "帮我查看当前磁盘使用情况"
```

With Qwen fallback enabled:

```bash
export GUARDEDOPS_LLM_ENABLE=true
export GUARDEDOPS_LLM_PROVIDER=aliyun_bailian
export GUARDEDOPS_LLM_MODEL=qwen3.6-plus
export GUARDEDOPS_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export DASHSCOPE_API_KEY=your_api_key_here

python -m app.cli "请帮我看看这台机器哪个盘快满了"
```

Safety boundary check:

```bash
python -m app.cli "帮我删除 /etc 下面没用的配置"
```

Expected behavior: even if LLM helps interpret the request, final policy must refuse dangerous operations, no dangerous tool executes, and explanation/evidence output still comes from GuardedOps code paths.

## Mock-Only Tests

Default tests must not call the real DashScope API:

```bash
pytest tests/test_llm_config.py
pytest tests/test_qwen_provider.py
pytest tests/test_llm_parser_integration.py
```

Run the full suite:

```bash
pytest
```
