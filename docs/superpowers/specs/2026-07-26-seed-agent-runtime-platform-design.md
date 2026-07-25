# Seed Agent Runtime Platform Design

日期：2026-07-26  
状态：待用户书面复核

## 1. 目标

在 `agent-runtime-lab` 中构建一个缩比但真实的 Agent 执行平台，重点证明：

- Agent 的长任务可以被确定性记录、暂停、恢复和重放；
- Tool 调用受到明确授权、Workspace 和 Sandbox 边界约束；
- 崩溃、超时、重复投递和部分成功不会静默产生重复副作用；
- Runtime 能产生稳定、可评测的 Trace 和执行证据；
- 真实模型可以通过 Adapter 接入，但模型不是状态与权限的权威；
- CodeOwnership 可以作为可插拔策略展示 Human Gate，而不是 Runtime 地基。

该项目面向 Seed Agent Systems、Agent Evaluation、AI Coding Infrastructure 和
AI Infra 实习/校招能力证据。它不尝试复刻 Claude Code，也不声称具备生产集群规模。

## 2. 范围

### 2.1 必须完成

1. 可导入、可测试、可安装的 Python 3.11 项目基线。
2. 类型化 Event、State、Tool Request、Tool Receipt 与错误模型。
3. 纯函数 Reducer、确定性 Replay、终态不变量和幂等语义。
4. 持久化 Run、Event 与 Receipt 的最小数据路径。
5. Tool Registry、Authorization、Executor 和 Verification。
6. 独立 Workspace 与一条真实受限执行路径。
7. Queue、Worker、Lease、Heartbeat、Timeout 和 Cancellation 的缩比实现。
8. Crash/Resume、重复投递、越权和损坏日志的 Failure Injection。
9. Fake Provider 与一个真实 OpenAI-compatible Provider Adapter。
10. 稳定 Trace Schema、Metrics 和可复现实验报告。
11. 从 Trace 提取 Trajectory，并运行自动 Grader 与 Outcome Reward 管线。
12. CodeOwnership Policy Adapter 的完整旗舰案例。

### 2.2 求职加分项

- Docker Sandbox 的 CPU、内存、网络和文件挂载限制；
- OpenTelemetry 兼容 Trace 导出；
- PostgreSQL 存储 Adapter；
- 小规模真实 Coding Task 数据集；
- Best-of-N、Rejection Sampling、LoRA 或 GRPO 小规模训练实验；
- 对 Multi-SWE-bench、SandboxFusion、vLLM、SGLang 或相关项目的真实 Issue/PR。

### 2.3 暂不实现

- 通用 Coding Agent 产品；
- Web Dashboard 或 IDE 插件；
- 多 Agent 协作框架；
- 长期记忆和通用 RAG；
- Kubernetes、Firecracker 或云平台复刻；
- 大规模强化学习训练；
- 大而全的 Tool Catalog。

## 3. 架构

```text
User / CodeOwnership Skill
             |
             v
      Runtime API / Session
             |
             v
   Policy + Authorization Gate
             |
             v
     Durable Reliability Kernel
 Event Store -> Reducer -> State -> Replay
             |
             v
       Orchestrator / Queue
       Lease / Worker / Cancel
             |
             v
       Tool Execution Boundary
 Registry -> Workspace -> Sandbox -> Receipt
             |
             v
        Verification / Evidence
       Trace -> Metrics -> Report
```

Runtime Core 不依赖具体模型、具体 Tool、CodeOwnership 或 Agent Eval 仓库。
所有外围能力通过 Protocol/Adapter 接入。

## 4. 核心组件

### 4.1 Domain Models

定义稳定的领域对象：

- `RunId`、`EventId`、`ToolCallId`、`IdempotencyKey`；
- `ExecutionEvent`：不可变事实，包含版本、序号、类型、时间和 Payload；
- `RunState`：由 Event 派生的当前状态；
- `ToolRequest`：模型或 Planner 提出的动作；
- `AuthorizationDecision`：允许、拒绝或等待用户；
- `ToolReceipt`：工具执行结果及副作用证据；
- `VerificationResult`：任务是否满足完成条件；
- 类型化的 Retryable、Terminal、Policy 和 Corruption 错误。

### 4.2 Reducer 与 Replay

`reduce(state, event) -> state` 是无 I/O 的纯函数。它不能访问数据库、文件、
网络、时钟或随机数。

必须保持：

- 同一事件序列永远派生相同状态；
- Event 序号连续且 Event ID 唯一；
- 非法状态转换被拒绝；
- Terminal State 不接受新的执行事件；
- 重复 Event 不改变最终状态；
- Replay 结果与在线执行状态一致。

Snapshot 仅是 Replay 优化，不能成为事实来源。

### 4.3 持久化

第一版采用 SQLite，原因是它能在单机学习环境中提供事务、约束和故障恢复，
同时避免过早引入外部服务。

持久化接口与 SQLite 实现分离，后续可以增加 PostgreSQL Adapter。核心表：

- `runs`
- `events`
- `tool_intents`
- `tool_receipts`
- `leases`
- `snapshots`（后置）

数据库事务只能保证 Runtime 内部记录原子性，不能让任意外部副作用与数据库
天然形成一个原子事务。因此外部工具必须接受稳定幂等键，或者提供可验证的
去重/补偿语义。

### 4.4 Tool Execution

固定执行顺序：

```text
validate request
-> authorize
-> persist intent
-> acquire execution claim
-> execute with idempotency key
-> persist receipt
-> verify postcondition
-> append completion/failure event
```

第一批工具只包含：

- `read_file`
- `write_file`
- `run_tests`

Fake Tool 先验证 Runtime 合同；真实文件和测试工具在合同通过后加入。

### 4.5 Workspace 与 Sandbox

每个 Run 获得独立 Workspace。所有路径在执行前被解析并验证位于 Workspace
根目录内，禁止通过 `..`、符号链接或绝对路径越界。

真实执行路径至少实现：

- 独立工作目录；
- 命令白名单或受控命令模型；
- Timeout 与进程树终止；
- stdout、stderr、exit code 留证；
- 文件变更 Diff；
- 环境变量和 Secret 脱敏；
- 可配置的 CPU、内存和网络限制。

路径检查不是 Sandbox 的替代品。Docker/受限子进程负责执行隔离，
Authorization 负责业务权限，二者必须同时存在。

### 4.6 Queue、Worker 与 Lease

缩比编排层采用 API/Worker 分离：

- API 创建 Run 和取消请求；
- Queue 暴露待执行工作；
- Worker 使用有期限 Lease 领取工作；
- Heartbeat 延长 Lease；
- Worker 崩溃或 Lease 过期后，工作可以重新领取；
- Cancellation Token 沿 Runtime、Tool、Sandbox 传播；
- 并发限制与 Backpressure 防止无限领取。

第一版仍可使用 SQLite Queue，但接口不能依赖 SQLite 私有语义。

### 4.7 Model Provider

Provider 只负责：

- 接收规范化上下文；
- 返回消息或 Tool Request；
- 报告 token、延迟和原始 Provider 元数据。

顺序：

1. `FakeModelProvider`
2. `OpenAICompatibleProvider`

模型输出永远需要 Schema 校验；不存在的 Tool、非法参数和空响应必须成为
显式失败，而不是由 Runtime 猜测修复。

### 4.8 Verification

任务完成由 Verifier 决定，不由模型的自然语言决定。Coding Task 至少检查：

- 目标测试；
- 允许的文件变更范围；
- 工具 Receipt 完整性；
- 必需 Gate；
- 没有未处理的 Policy Violation；
- 没有仍在运行或等待的步骤。

### 4.9 Trace、Metrics 与 Eval

Runtime 输出版本化 Trace，不直接依赖 `agent-eval-lab`：

```text
run.created
model.response
tool.requested
tool.authorized | tool.denied
tool.started
tool.completed | tool.failed
verification.completed
run.paused | run.resumed
run.completed | run.failed | run.cancelled
```

核心指标：

- Task Success
- Test-verified Completion
- Resume Success
- Replay Consistency
- Duplicate Side-effect Count
- Unauthorized Action Block Rate
- Tool Error Recovery
- Runtime Steps
- Latency
- Token/Cost

## 5. 关键数据流

### 5.1 正常执行

1. API 创建 Run 并写入 `run.created`。
2. Reducer 派生 `READY` 状态。
3. Worker 领取 Lease。
4. Provider 提出 Tool Request。
5. Runtime 校验并授权。
6. 写入 Tool Intent。
7. Executor 在 Workspace/Sandbox 中执行。
8. 保存 Receipt 与完成 Event。
9. Verifier 检查后置条件。
10. Runtime 进入下一步或 Terminal State。

### 5.2 崩溃恢复

恢复时不直接相信缓存状态：

1. 读取 Run Event；
2. 校验连续性和完整性；
3. Replay 派生状态；
4. 检查未闭合 Tool Intent；
5. 使用幂等键查询/恢复 Receipt；
6. 仅在安全语义明确时重试；
7. 写入恢复事件并继续。

### 5.3 用户 Gate

Policy 可以返回 `WAITING_FOR_USER`。Runtime 保存 Gate、暂停 Lease 中的业务执行，
收到结构化答案后验证并追加 Event。CodeOwnership 仅通过该接口提供
`AUTO/PAIR/USER_GATE` 策略，不能绕过 Runtime 状态机。

## 6. 错误处理

错误按责任域分类：

- Model：空响应、非法 Tool、非法 Schema；
- Policy：拒绝、缺少 Gate、越权；
- Runtime：非法转换、重复 Event、Lease 冲突；
- Persistence：事务失败、日志损坏；
- Tool：参数错误、异常、非零退出；
- Sandbox：启动失败、资源超限、网络/文件违规；
- Verification：测试失败、证据缺失、模型误报完成；
- Cancellation：用户取消、Deadline、上游取消。

所有错误必须：

- 类型化；
- 进入 Trace；
- 明确是否可重试；
- 保留原始 Cause；
- 不泄露 Secret；
- 不以捕获所有异常后继续执行的方式掩盖失败。

## 7. 测试与证据

### 7.1 测试层次

- Domain Unit Tests：Reducer、不变量、Schema；
- Persistence Contract Tests：事务、唯一约束、损坏检测；
- Tool Contract Tests：授权、Receipt、幂等；
- Sandbox Integration Tests：路径、超时、资源与日志；
- Orchestration Tests：Lease、Heartbeat、取消、重新领取；
- Failure Injection Tests：在关键持久化窗口主动崩溃；
- End-to-End Tests：Fake Provider 和真实 Provider Smoke；
- Eval Experiments：Baseline Tool Loop 与 Reliability Runtime 对比。

### 7.2 必须演示的失败窗口

1. Tool 执行前崩溃；
2. Tool 成功后、Receipt 保存前崩溃；
3. Receipt 保存后、Completion Event 前崩溃；
4. 重复消息投递；
5. Worker Lease 过期；
6. 越权路径；
7. Tool Timeout；
8. Event 损坏；
9. 测试失败但模型声称完成；
10. 用户取消传播。

### 7.3 完成证据

每个里程碑必须同时具备：

- 实现；
- 自动测试；
- Ruff/CI；
- 可运行 Demo；
- Trace 或报告；
- 文档与 ADR；
- 用户理解验收。

代码存在但没有验证和解释，不算完成。

## 8. 教学与所有权

AI 负责实现、测试、重构、CI 和机械文档。用户当前远程期间不要求手写代码，
但每个里程碑后必须完成 10–20 分钟理解验收。

用户最终必须能解释：

- Event、State、Reducer 与 Replay；
- 幂等键和三个崩溃窗口；
- Authorization 与 Sandbox 的区别；
- Queue、Lease、Heartbeat 与 Cancellation；
- Trajectory、Grader 和 Reward；
- Model、Runtime、Tool、Sandbox 与 Protocol Failure 的区别；
- 每项实验实际证明了什么。

验收方式包括预测状态、阅读关键 Diff、解释 Trace 和分析失败案例。

## 9. 与其他项目的关键交互

本设计不管理其他项目进度，只定义边界：
Runtime 不修改其他仓库，也不以其他仓库的日常计划作为自身推进前提。

### 9.1 Agent Eval Lab

**核心接口一：Runtime Trace/Eval Contract。**

- Runtime 拥有并导出版本化 Trace、JSON Schema、Fixture 和 Run Report；
- Trace 至少覆盖模型决策、授权、工具执行、恢复、验证和终态；
- Agent Eval 只通过公开文件/API 读取 Trace，不访问 Runtime 数据库；
- Agent Eval 负责聚合、基线对比、指标计算和失败归因；
- 两个仓库不直接互相 import；
- Schema 变更通过版本号、契约测试和兼容样例管理。

Agent Eval 达到以下接入状态后，才接入真实跨仓库评测：

1. 能加载并校验 Runtime Trace Schema；
2. 能识别 Model、Runtime、Tool、Sandbox、Protocol 和 Verification Failure；
3. 能聚合 Task Success、Resume、Replay、重复副作用和越权指标；
4. 能输出可复现实验报告。

在这些能力未完成前，Runtime 使用本仓库内的 Contract Fixture 和最小报告器独立验证，
不接管 Agent Eval 的进度。

### 9.2 Decoder Inference Lab / vLLM

**核心接口二：OpenAI-compatible Model Provider Contract。**

- Runtime 通过 Provider Protocol 调用真实模型服务；
- Provider 输入是规范化消息、Tool Schema、Deadline 和 Cancellation；
- Provider 输出是规范化消息、Tool Request、Usage 和 Backend Fingerprint；
- Provider 记录 backend/version/config、token、延迟和请求 ID；
- Runtime 不实现模型训练、Attention、KV Cache 或调度器；
- Decoder/vLLM 不访问 Runtime 的 Event Store、Queue 或 Policy。

真实模型接入需要达到以下状态：

1. Decoder 项目完成 KV Cache、Prefill/Decode 等价性和基础 Profiling，用于解释模型侧边界；
2. 至少一个 vLLM/OpenAI-compatible Endpoint 可以完成确定性 Smoke Test；
3. Provider 能保留请求、响应、流式事件、错误和环境指纹证据；
4. Runtime 能区分 Provider、Protocol、Tool 和自身故障。

这些条件不是 R1–R5 的阻塞项。条件满足前，Runtime 使用 Fake Provider 和录制 Fixture；
Runtime 只声明接口与接入门槛，不管理 Decoder 或 vLLM 的实施进度。

### 9.3 CodeOwnership Skill

- Skill 通过 Policy、Gate 和 Evidence 接口接入；
- Runtime 保持策略无关；
- CodeOwnership 不拥有 Event Store、Executor 或 Replay；
- Policy Adapter 位于 Runtime 源码，安装型 Skill 资产位于仓库内独立目录；
- 由 AI 完成 Skill、Adapter、脚本、测试、文档和演示实现；
- 必须包含 `AUTO/PAIR/USER_GATE`、Protected Scope、Gate Evidence 和 Knowledge Report；
- Skill 需要完成 Prompt-only 与 Runtime-enforced Policy 对比实验；
- 用户不需要远程手写代码，但必须理解 Policy、Gate 和 Runtime Enforcement 的区别。

## 10. 实施顺序

1. S0：可导入包、环境、测试、Ruff、CI 和进度基线。
2. R1：Domain Models、Reducer、Replay、不变量。
3. R2：SQLite Event Store、Intent/Receipt 和 Crash Recovery。
4. R3：Authorization、Tool Registry、Workspace 与 Fake Tool。
5. R4：真实 Tool、受限执行和 Sandbox。
6. R5：Queue、Worker、Lease、Heartbeat、Timeout、Cancellation。
7. A1：Fake Provider 到真实 OpenAI-compatible Provider。
8. A2：Coding Task、Trace、Metrics、Failure Injection 和对比报告。
9. A3：必做 Trajectory、Grader 和 Outcome Reward；算力允许时再做 RL-lite。
10. P1：完整 CodeOwnership Skill、Policy Adapter 和对比实验。
11. O1：开源协作、技术文章、Demo 和求职材料。

每一阶段只在前一阶段合同和证据通过后进入下一阶段。

## 11. 成功标准

项目完成时，应能够用一个可复现 Demo 证明：

- Agent 可以在真实受限环境中执行 Coding Task；
- Runtime 崩溃后恢复且不重复已成功副作用；
- 非法 Tool 和越权路径被确定性拦截；
- Worker 失败、超时和取消不会留下模糊状态；
- 模型误报完成会被 Verifier 拒绝；
- Trace 可以 Replay，并由外部 Eval 消费；
- Trace 可以生成 Trajectory，并由自动 Grader 产生可解释的 Outcome Reward；
- 真实模型与 Fake Provider 共享同一 Runtime 合同；
- CodeOwnership 可以插拔而不污染核心；
- 用户能够独立讲解核心机制、失败窗口和实验结论。

