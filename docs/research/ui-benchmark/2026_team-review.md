# Aplus 2026 全域产品与开源项目深度对标报告

> 截至2026-11-01 · 内部讨论资料，完整图文见同名 HTML/PDF。

## 六个需要团队讨论的决策

### A. 多图变成真正的工作区
- 目标：每格不再重复工具；三种常用布局一键使用；活动图格/联动/绘图范围明确。
- 验收：用户在九宫格下看到K线有效面积明显增加；同股日/周/5m联动可解释。
- 约束：体验收益高；需要跨 Tk/WebView 命令架构。

### B. 所有研究结果都能落回具体K线
- 目标：从净R、持仓K线数、TOP亏损点开就进入交易样本及当时回放。
- 验收：报告→样本≤2步，返回时研究参数和滚动位置不丢。
- 约束：复用现有回测数据和图表；先不改指标口径。

### C. 风险先于收益
- 目标：总开放风险及新增机会可用风险、同时持仓数量、执行纪律分开表达。
- 验收：合规亏损仍可被标记为正确执行；超风险持仓直接可见。
- 约束：需核对交易管理真实数据模型。

### D. 案例是外部记忆而不是管理后台
- 目标：盘前直接搜索旧图；OCR与视觉相似只作增强；原始图片保真。
- 验收：从Discord粘贴7张→第二天microDB找到原图；500张场景仍可用。
- 约束：权限、持久化和索引可靠性为先。

### E. 训练针对市场背景和失效证据
- 目标：高质量真实案例、冻结未来行情、用户独立判断、反馈与重复训练。
- 验收：用户能指出区间高二与强趋势高二的区别，而非猜对按钮。
- 约束：需要数据完整性和内容审核，不应先追积分。

### F. UI语义一致，五模块各自专业
- 目标：统一导航/状态/空态/错误/保存反馈；保留各自高效布局。
- 验收：新用户不用说明理解第一步；老用户两次点击内完成高频动作。
- 约束：可按组件与模块逐步实施，不搞整套架构大迁移。

## Aplus与竞品主要差距

| 能力 | 现状 | 目标 | 优先级 |
|---|---|---|---|
| 盘前工作区工具栏 | 每格重复完整工具 | 工作区唯一顶/左栏，活动图路由 | P0 |
| 多图联动 | 仅局部统一切换 | 5项独立控制+联动组 | P0 |
| 命名布局与恢复 | 1/4/6/9宫格内存设置 | 自建/复制/保存/恢复 | P1 |
| 对象快捷栏 | 11类专属能力已有 | 仅选中显示，收藏栏独立 | P0 |
| 跨布局人工画线 | 同身份整文档共享边界 | 对象ID+scope+冲突/撤销 | P1 |
| 候选与图表联动 | 已有结果和关注列表 | 布局模板、状态回执、稳定切图 | P1 |
| 回测分析与下钻 | 已有记录/分布/回放 | 分布→样本→图表上下文 | P1 |
| 风险敞口/纪律 | 管理模块已存在 | 总风险贡献+纪律独立评价 | P1 |
| 案例收录与关键词找回 | 成长空间已有一定闭环 | 1000图检索/批量/权限验证 | P1 |
| 案例相似形态检索 | 当前没有可信匹配器 | 分层检索+理由+明确无结果 | P2 |
| PA 盲训与前后时点 | 训练原型/有限业务 | 真实冻结行情和反馈证据 | P1 |
| 知识宇宙真实性 | 已有可视化 | 关系必须映射真实案例 | P2 |
| 跨模块上下文 | 独立入口与部分桥接 | 从盘前图直接找案例/对照并返回 | P0 |
| 全局UI与安全状态 | 多套 Tk / WebView 样式 | 一致的控件行为、状态与误操作保护 | P1 |
| 策略验证护栏 | 现有R0-R3/冻结策略 | 新增前视偏差与回放一致性专项 | P1 |

## GitHub专项参考

| 项目 | 类型 | 评估结论 |
|---|---|---|
| [skill-xingtai-catcher](https://github.com/quantskills/skill-xingtai-catcher) | 搜索/Agent Skill | 仅作灵感/验证；不是离线完整引擎；A股/期货与日线/60m适用范围、第三方上传及GPL-3.0；需核查接口版本和隐私 |
| [tradingview/lightweight-charts](https://github.com/tradingview/lightweight-charts) | 图表底座 | 局部评估；并非完整Supercharts多图工作区、绘图/布局须自行建；注意 NOTICE 署名 |
| [klinecharts/KLineChart](https://github.com/klinecharts/KLineChart) | 图表底座 | 局部评估；API/现有WebView集成与历史画线迁移成本必须先验证；Apache-2.0 |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | 批量回测 | 研究参考；向量化回测与事件驱动订单语义有差异；冻结策略口径不能迁移时改变 |
| [ranaroussi/quantstats](https://github.com/ranaroussi/quantstats) | 研究报告 | 报告参考；周期收益指标与单笔交易的R/胜率不是同一个统计对象 |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | 策略引擎 | 架构参考；重平台；非小范围UI集成，迁移风险高 |
| [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | 策略引擎 | 架构参考；Rust/Python复杂性及LGPLv3，A股交易规则与本工程迁移代价高 |
| [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) | 风控/回测 | 方法参考；以币种/自动交易为主；不直接复制执行模型 |
| [mementum/backtrader](https://github.com/mementum/backtrader) | 回测引擎 | 方法参考；维护活跃度/版本兼容、订单事件语义需专项评估 |
| [jesse-ai/jesse](https://github.com/jesse-ai/jesse) | 回测/可视化 | 原型对标；加密交易生态为主，不能直接满足A股T+1 |
| [vnpy/vnpy_ctastrategy](https://github.com/vnpy/vnpy_ctastrategy) | CTA链路 | 方法参考；国内适配更近，但Aplus当前非自动实盘引擎；避免范围漂移 |
| [microsoft/qlib](https://github.com/microsoft/qlib) | AI研究流程 | 长期关注；数据与模型验证门槛高，避免将预测ML包装为确定PA信号 |
| [akfamily/akshare](https://github.com/akfamily/akshare) | 行情数据 | 辅助数据对照；来源稳定性、复权、时间戳和服务条款必须独立校验 |
| [pbakaus/impeccable](https://github.com/pbakaus/impeccable) | UI设计规范/skill | 研发流程参考；改善设计评审方法，无法代替真实用户测试；Apache-2.0 |
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | AI记忆/检索 | 概念研究；性能数字多针对托管能力；私密案例检索应优先本地、可解释 |

## 主要证据链接

- [R01 Aplus 主窗口代码](https://github.com/sysujonathan/Aplus/blob/main/gui/main_window.py)（代码核查）
- [R02 Aplus 多图容器](https://github.com/sysujonathan/Aplus/blob/main/gui/chart_panel.py)（代码核查）
- [R03 TV差异报告（含Issue#92）](https://github.com/sysujonathan/Aplus/blob/main/docs/TV真实操作差异对照.md)（代码+有限实测）
- [R04 Aplus 策略迭代页面](https://github.com/sysujonathan/Aplus/blob/main/gui/strategy_iteration.py)（代码核查）
- [R05 Aplus 盘后回测页面](https://github.com/sysujonathan/Aplus/blob/main/gui/afterhours.py)（代码核查）
- [R06 Aplus 成长空间原型CSS](https://github.com/sysujonathan/Aplus/blob/main/gui/pa_growth/growth.css)（代码核查）
- [R07 TradingView 多图功能](https://www.tradingview.com/support/solutions/43000629990-leveraging-multi-chart-layouts-in-your-analysis/)（官方说明）
- [R08 TradingView 图表联动](https://www.tradingview.com/support/solutions/43000629992-how-to-sync-the-charts-of-my-layout/)（官方说明）
- [R09 TradingView 布局管理](https://www.tradingview.com/support/solutions/43000746975-tradingview-layouts-a-quick-guide/)（官方说明）
- [R10 TV 分屏模式](https://www.tradingview.com/support/solutions/43000753069-simple-steps-to-split-view-mode/)（官方说明）
- [R11 Koyfin 自定义工作台](https://www.koyfin.com/features/custom-dashboards/)（官方说明）
- [R12 TC2000 四周期](https://help.tc2000.com/m/69401/l/743982-factory-default-layout-workspace)（官方说明）
- [R13 Sierra Chart 图表簿](https://www.sierrachart.com/index.php?page=doc%2FChartbooks.html)（官方说明）
- [R14 TrendSpider MTFA](https://help.trendspider.com/kb/automated-technical-analysis/multi-timeframe-analysis)（官方说明）
- [R15 TradeZella Replay](https://help.tradezella.com/en/articles/7898284-trade-replay-2-0-a-powerful-tool-for-analyzing-trades-second-by-second)（官方说明）
- [R16 TradesViz](https://www.tradesviz.com/about/)（官方说明）
- [R17 Edgewonk Tiltmeter](https://edgewonk.zendesk.com/hc/en-us/articles/360010150259-The-Tiltmeter)（官方说明）
- [R18 TraderLion 历史图册](https://traderlion.com/model-book/)（官方说明）
- [R19 Heptabase 组织机制](https://wiki.heptabase.com/organize-knowledge-and-projects)（官方说明）
- [R20 ChessTempo训练](https://www.chesstempo.com/manual/en/manual.html)（官方说明）
- [R21 Brilliant](https://brilliant.org/faq/)（官方说明）
- [R22 WaniKani SRS](https://knowledge.wanikani.com/wanikani/srs/)（官方说明）
- [R23 Raindrop 帮助](https://github.com/raindropio/help/blob/master/bookmarks.mdx)（官方说明）
- [R24 Eagle 素材管理](https://en.eagle.cool/blog/post/why-eagle)（官方展示）
- [R26 TraderSync回放](https://tradersync.com/support/how-do-i-replay-an-existing-trade/)（官方说明）
- [R27 Chartlog日记](https://help.chartlog.com/en/articles/5503851-the-journal-view)（官方说明）
- [R25 交易风险前视验证](https://www.freqtrade.io/en/stable/lookahead-analysis/)（官方开源文档）