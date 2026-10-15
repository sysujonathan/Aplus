# PR47 / MTR V36.3 历史迁移基线

来源：`sysujonathan/Aplus` 精确提交 `29d04e491b031ba3824fa015a1ca0144d0667883`。
只保存该提交的两个旧MTR文件、冻结清单和旧适配器，用于隔离缓存迁移测试，绝不注册到正式策略目录。

`.txt` 采用可审阅的UTF-8/LF。恢复到临时目录时，两个`core/`源文件恢复原CRLF，其余恢复LF，随后按以下原始字节SHA-256核对：

| 原路径 | SHA-256 |
|---|---|
| core/strategies/mtr_strategy.py | 68720522848c2fffc4bff505de77716a5256d48a88161a8d03bd19f7415e406a |
| core/strategies/mtr_structural_v35.py | 9705da38307f6b68f6bcc1a9b5672765721074ef0a24977c963a8c3bdfd9f31e |
| frozen_manifest.json | 8efd4dcc9d2e3e83d90ab48bb33a1b0d78cd188b337f6e99277517b1d26e3217 |
| workbench/strategies.py | a3ebd4ba33a23ab2c8a4327171b6986c2db4dd871723011b7295376d7f82d3c9 |

测试不依赖Git历史深度、不联网。先在独立进程用旧冻结实现／适配器和当前Service生成真实旧格式判断缓存，再在当前V36.4代码中扫描同一个临时行情快照。仅结构引擎的阶段输入为明确合成场景，信号适配器、缓存、完整性检查、事务和候选加载均实际执行；不修改仓库中任何冻结文件，不读写用户数据仓。
