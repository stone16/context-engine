# ContextEngine

> A multi-tenant context delivery engine: connect your team's knowledge sources upstream, deliver **authorized, evidence-backed, budget-bounded** context packages to agents and IM bots downstream.

多租户上下文交付引擎——上游连接团队知识源(飞书 / Slack / Google Docs / 企业微信),下游把「经过授权、带证据、有预算」的 ContextPackage 交付给 agent 应用与 IM bot(飞书群聊问答优先)。

**当前状态**:设计阶段(pre-M0),尚无可运行代码。整体计划见 [PLAN.md](./PLAN.md)。

## 为什么做这个

现有知识库产品回答的是「怎么存、怎么搜」;RAG 工具链回答的是「怎么找到最近的 chunk」。都没有回答两个更难的问题:

1. **这个用户此刻有权知道什么?** —— 权限感知的检索与交付:索引过滤只做候选收窄,每条进入交付包的内容在交付前逐条回权威库复核授权;撤权在下一次查询即生效。
2. **知识库由谁来组织?** —— Agent 承担 80% 的组织成本(语义去重、过期标记、术语沉淀),用户只做 audit;所有 AI 产物先提案、经确认、再原子发布,绝不直写生产。

## 三条硬底线(release veto,不是分数)

- 无授权证据泄漏 = 0(Unauthorized Evidence = 0)
- 跨租户影响 = 0(wrong-Organization effect = 0)
- 缺失租户上下文一律 fail closed

任何功能收益不能抵消其中任何一条的失败。每次发布出具逐条不变量的 pass/fail 报告。

## License

TBD(设计阶段;在首个可运行版本前确定)。
