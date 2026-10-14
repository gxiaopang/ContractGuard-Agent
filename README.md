# ContractGuard Agent

ContractGuard Agent 基于 [Waku](https://github.com/ShenSeanChen/waku-agent)，
新增合同条款提取、风险评估和可复用的审查记忆。
它从合同原文中提取条款证据，生成审查结果，并保存结构化数据和 Markdown 报告。
普通审查需要人工确认或纠正后，才会将接受的发现写入审查记忆。

## 主要改进

- **合同审查流程。** 通过 `review_contract` 工具完成合同解析、条款上下文检索、
  证据提取和高／中／低风险评估，并在有参考指引时进行对比。
- **原文证据校验。** Python 校验引用文本及其在原文中的位置，
  校验通过后才将结果写入报告或记忆。报告分别记录条款缺失和审查阶段失败。
- **人工复核。** Reviews 页面支持确认、纠正和拒绝报告。
  系统保留原始结果、接受后的报告和复核记录；确认与纠正操作不调用模型，拒绝不写入审查记忆。
- **领域记忆与技能。** 新增十类条款审查技能，结合语义记忆中的条款定义与指引，
  以及情节记忆中的历史审查结果。检索门控按需选择上下文，并排除当前合同的历史记录。
- **作用域记忆。** 可选升级按工作空间、合同类型、代表方、政策版本和有效期筛选适用记忆，
  保存已批准的纠正记录，并支持政策与经验规则的批准、撤销和替代。
- **可复现评测。** 基于 CUAD，在独立数据目录中对比基线、仅语义记忆和完整记忆三种配置。
  使用固定的文本片段匹配规则计算精确率、召回率和 F1，标准答案不进入模型输入或记忆。
- **记忆升级评测。** 通过生成用例或本地用例，对比无检索记忆、仅语义记忆、
  已批准历史和历史加纠正四组配置，支持冻结记忆和评分后加入反馈的顺序实验。
- **审查仪表盘。** Reviews 页面展示已保存的报告、风险统计、审查进度、
  记忆快照和条款提取指标，并提供人工复核与记忆管理操作。

这些改进复用 Waku 的智能体循环、模型适配器、SQLite 记忆和本地仪表盘。
设置 `WAKU_CONTRACT_REVIEW=1` 即可启用合同审查。
同时设置 `WAKU_CONTRACT_MEMORY_UPGRADE=1` 可启用作用域记忆与纠正记录。

## 快速开始

克隆并安装本项目：

```bash
git clone https://github.com/gxiaopang/ContractGuard-Agent.git
cd ContractGuard-Agent
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

运行包含五份示例合同的离线演示：

```bash
python scripts/demo_review.py --contracts 5 --show-memory-growth --seed 42
```

演示使用预设响应，无需 API Key。在另一个终端执行演示输出的
`WAKU_HOME=... python -m waku dashboard` 命令，再打开
`http://localhost:7777/#reviews` 查看审查报告和记忆增长。
演示使用隔离的评测记忆，不经过普通审查的人工确认流程。

进行交互式审查时，先在 `.env` 中配置模型服务和 API Key，再运行：

```bash
export WAKU_CONTRACT_REVIEW=1
python -m waku
# 也可以使用仪表盘：
python -m waku dashboard
```

将合同文本发给智能体，并指定需要审查的条款。
审查工具支持最多 200,000 个字符的纯文本输入。
生成报告后，在仪表盘的 Reviews 页面选择 `Confirm and save`、
`Correct and confirm` 或 `Reject` 完成人工复核。
系统只将确认后的正向条款发现保存为审查记忆，不自动学习语义规则。

启用作用域记忆时，在启动智能体或仪表盘前设置两个开关：

```bash
export WAKU_CONTRACT_REVIEW=1
export WAKU_CONTRACT_MEMORY_UPGRADE=1
python -m waku dashboard
```

系统只检索已批准且适用于当前审查的记录。历史、纠正和经验记录缺少角色或政策版本时，
不会进入作用域检索；过期、已撤销或已被替代的记录也不会作为审查指引。

## 记忆升级评测

运行默认的离线生成用例，无需外部数据集、人工标注或 API Key：

```bash
python scripts/run_eval.py --upgrade --repetitions 2 --seed 42
python scripts/run_eval.py --upgrade --experiment sequential --seed 42
```

每组配置使用新的独立记忆目录。冻结实验保留初始记忆，顺序实验在评分后加入
明确标记的预设反馈。结果记录提取指标、检索决策、回归、延迟和组间差异。
这些离线结果验证实现与实验流程；真实模型的审查质量和记忆收益仍未测量。
使用 `--live` 会调用模型，同时添加 `--judge` 会增加分项质量评价。
尚未经过人工校准的模型评价不能证明法律判断正确，工程检查通过也不代表质量评测通过。

## 项目文档

- [架构设计](docs/contractguard/architecture.md) 说明各项改进对应的代码模块。
- [审查工具链](docs/contractguard/toolchain.md) 介绍审查阶段和存储方式。
- [评测方案](docs/contractguard/evaluation.md) 说明 CUAD 评测和记忆消融实验。
- [演示指南](docs/contractguard/demo.md) 介绍仪表盘使用方式和生成文件。
- [人工复核设计](docs/contractguard/human-review-design.md) 说明人工决策、纠正与记忆写入边界。
- [记忆与评测升级设计](docs/contractguard/memory-eval-design.md) 说明作用域、记忆生命周期和评测约束。
- [实验结果](docs/contractguard/results.md) 记录离线实验和复现命令。

## 致谢与许可证

本项目基于 [ShenSeanChen](https://github.com/ShenSeanChen) 开发的 Waku。

合同审查实现与 Waku 核心代码采用 [MIT 许可证](LICENSE)。

`hosted/` 目录采用 [Elastic License 2.0](hosted/LICENSE)。

Waku 名称、标志和设计系统遵循 [LICENSE-BRAND](LICENSE-BRAND)。
