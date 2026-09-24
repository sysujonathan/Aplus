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
