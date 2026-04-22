# GuardedOps

GuardedOps 是一个安全对话式 Linux/SSH 运维代理项目。

本项目用于 AI Hackathon 2026《操作系统智能代理》赛题，目标是在真实 Linux/SSH 运维场景中探索可运行、可验证、可审计的安全代理形态。

当前阶段处于仓库初始化阶段，仅建立基础目录、工程配置和后续开发入口，不代表核心能力已经完成。

核心安全边界：

- 禁止 arbitrary shell。
- 禁止 raw command mode。
- Prompt 不作为最终风控边界。
- 执行层只允许调用白名单工具。

后续开发将遵循 `agent.md` 与 `architecture_constraints.md` 中定义的任务边界、安全约束和状态更新规则。

更多能力、使用方式、架构说明和演示材料将在后续阶段补充。
