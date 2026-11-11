# PR47 / MTR V36.3 历史迁移基线

来源：`sysujonathan/Aplus` 精确提交 `29d04e491b031ba3824fa015a1ca0144d0667883`。
保存该提交的两个旧MTR文件、冻结清单和旧适配器，用于隔离缓存迁移测试，绝不注册到正式策略目录。

`.txt` 采用可审阅的UTF-8/LF。恢复到临时目录时，所有`core/`源文件恢复原CRLF，其余恢复LF（下文补档的 `config/rating_factors.json` 显式恢复原CRLF），随后按以下原始字节SHA-256核对：

| 原路径 | SHA-256 |
|---|---|
| core/strategies/mtr_strategy.py | 68720522848c2fffc4bff505de77716a5256d48a88161a8d03bd19f7415e406a |
| core/strategies/mtr_structural_v35.py | 9705da38307f6b68f6bcc1a9b5672765721074ef0a24977c963a8c3bdfd9f31e |
| frozen_manifest.json | 8efd4dcc9d2e3e83d90ab48bb33a1b0d78cd188b337f6e99277517b1d26e3217 |
| workbench/strategies.py | a3ebd4ba33a23ab2c8a4327171b6986c2db4dd871723011b7295376d7f82d3c9 |

测试不依赖Git历史深度、不联网。先在独立进程用旧冻结实现／适配器和当前Service生成真实旧格式判断缓存，再在当前V36.4代码中扫描同一个临时行情快照。仅结构引擎的阶段输入为明确合成场景，信号适配器、缓存、完整性检查、事务和候选加载均实际执行；不修改仓库中任何冻结文件，不读写用户数据仓。

## 评级依赖补档（Issue #145 / PR #146 CI 修复）

归档 V36.3 策略仍依赖旧评级模块。为让历史沙箱自包含，从删除前 main 提交
`e13d1c9709f59def5cd9726680b3cd1e0870075b` 提取以下原始文件；`.txt` 同样保存为
UTF-8/LF，测试恢复原始 CRLF 并按原始字节 SHA-256 校验。哈希也与上述归档
`frozen_manifest.json.txt` 中的对应记录一致。仅写入临时 historical-code/core，生产树不恢复评级引擎。

| 原路径 | SHA-256 |
|---|---|
| core/rating.py | 81b34141c6d797cd467ee62f27064a07466cb5b438e52d4b0513005728ca8ca5 |
| core/rating_core.py | 5fd4d32414a3d1a428f43d218c3281e261b8489017ca5ece6704ffe276de91a2 |

## 历史沙箱剩余依赖补档（PR #146 续篇）

以下 10 份文件来自 `e13d1c9709f59def5cd9726680b3cd1e0870075b`，原始字节 SHA-256 逐项等于历史清单。均以 UTF-8/LF 存档，在 historical-code 沙箱恢复 CRLF，恢复字节与来源完全一致（rating_factors.json 原件也是 CRLF，为原先 config 默认 LF 规则的显式例外）。仅重建历史依赖，不恢复生产评级代码，不修改清单或保护检查。

| 原路径 | SHA-256 | 清单核对 |
|---|---|---|
| core/formatter.py | ff4388c93a465ad75cd1365163c3c6cfa224e18dece8b8613814cccf04d9deea | PASS |
| core/strategies/awil_strategy.py | d84c5a19de70934c4a060e1498b54124aeea898864613aea21c8d3d5664323a2 | PASS |
| core/strategies/base.py | c5b4dadf7a574aff6544fa10a3ca88f172f26cf4846082ceadb387e8d3e648d2 | PASS |
| core/strategies/gap_h2_enhanced_strategy.py | 61387386bb2558e33543c1e1bb818b6cce10288e654a871036c6fdc37635f386 | PASS |
| core/strategies/gap_h2_strategy.py | 57e61feb07991feabca85b556ca3a6e5e66eb5258d6f3c4a58a16d753237a248 | PASS |
| core/strategies/gap_pinbar_strategy.py | 53f79f022e95aa52ab1351bc7285b7942664f25f66860fcd8f1c101cd4a1b9bf | PASS |
| core/strategies/monthly_range_break_strategy.py | 80bfcd8eae3ab527e8a625f33da811ae1e5726f23977c805643a7ff201144ac4 | PASS |
| core/strategies/structural_gap_strategy.py | 5d3627b27719bdcd8306692919e392951eadf3808a7a6820910934c55d0780c2 | PASS |
| core/strategies/three_k_strategy.py | cb7a908ecc18396b6617988e633a28963a257b3ffa27c31bcec8eee14e8ba9ee | PASS |
| config/rating_factors.json | 508d42deb24f90dfe9836da854c964c6b53e344cea156dbd81e192d1cff182c0 | PASS |
