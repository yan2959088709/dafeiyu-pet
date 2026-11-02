# -*- coding: utf-8 -*-
"""版本与上游基准的单一来源。

为什么单独一个模块：配置元信息、关于对话框、打包版本号、上游对照清单都读
同一处；分散成字面量后必然漂移，对不上「与上游哪一版对齐」这个基本问题。
"""

APP_VERSION = "0.4.0"

# 移植基准：PC2005-cloud/dsh-pet 的 main 分支
UPSTREAM_REPO = "PC2005-cloud/dsh-pet"
UPSTREAM_VERSION = "0.3.8"
UPSTREAM_BASELINE = "4a222e2"
UPSTREAM_BASELINE_DATE = "2026-10-28"

# 任务书给出的 d8ffa51 已被上游 force push 抹除（= 本基准 + voiceOn 字段 + 其 Revert）
UPSTREAM_REMOVED = ("d8ffa51", "384c1e2")
