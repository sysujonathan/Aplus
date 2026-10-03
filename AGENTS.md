# 新版 A 的维护约定

先读 README.md、docs/系统地图.md、docs/修改约束.md。与用户沟通使用交易员语言，先说影响，再说文件。

- 本项目是独立重建版本。第一阶段 core/strategies 及 frozen_manifest 中所有文件不可修改。
- 不触碰旧项目数据库。新数据只写运行目录；不要删除用户记录来解决升级问题。
- 新数据库结构只有 workbench/store.py 管理。
- 回测不写 observations/plans；不让演示数据冒充真实数据。
- 插件默认研究，验证和真实回测后须人工确认才能启用。
- 不新增下单能力或外部消息发送，除非用户明确要求。
- 修改用 apply_patch；检查用本项目 .venv/Scripts/python.exe -m pytest -q。
- 任何“已完成”报告须区分代码测试、真实数据测试与长期实际使用验收。

## UX 设计与界面维护

涉及本项目页面、交互、原型或界面文案时，使用项目 skill `.agents/skills/aplus-ux/SKILL.md`，按需读取其指向的 `docs/统一UX规范V1.md`。规则以用户要求和 `docs/修改约束.md` 为先；规范中的尺寸与比例是设计起点，不表示全应用已改造或验收。纯策略研究、纯性能计算修改无需加载 UX 规范，除非涉及用户体验。

## 多人协作约定（GitHub 是唯一事实源）

所有协作者（人类与 AI）都必须遵守；本节与 docs/修改约束.md 冲突时以后者为准。

- 一切以 GitHub main 为准。Google Drive 仅是无人值守冷备份，不作为协作来源；不要从 Drive 拿代码。
- 开工先 `git pull`；改动永远在特性分支上做（`git checkout -b 名字/主题`），不直接提交到 main——main 的修改只经 PR 合入。
- 提交前必须跑 `.venv\Scripts\python.exe -m pytest -q`，71 项全过才算完成；GitHub Actions 会复检，红了先修再继续。
- 提交说明格式「Aplus：改动摘要」；PR 描述按 docs/修改约束.md 写四件事：改什么、影响哪个场景、不应改变什么、怎么验收。
- 冻结红线不变：core/strategies 与 frozen_manifest 不可改；GUI 写库只准走 store 方法（schema 冻结）；frozen_manifest.json 按字节校验，任何编辑器/工具的行尾转换都可能破坏它（.gitattributes 已禁转换，勿移除）。
- 隐私红线：.workbuddy/、runtime/、密钥、持仓记录绝不入库；新增文件若含敏感内容须先确认再 add。
- 同文件冲突：让 AI 辅助解、人工确认；解不了升级给仓库主人（sysujonathan），不要强推（push -f 绝对禁止）。
