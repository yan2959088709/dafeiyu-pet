# -*- coding: utf-8 -*-
"""模型口径与峰谷计费：价格表 + 峰谷判定 + 单轮成本换算。

除 is_peak_hour() 不带参数时读取当前时刻外均为纯函数，可独立单测。
"""
import time

# ===== DeepSeek 配置 =====
DS_BASE_URL = "https://api.deepseek.com/v1"
DS_MODEL = "deepseek-v4-flash"

# ---- 峰谷定价表（2026-08-17 官方价，单位：元/百万 tokens）----
# 高峰 = 北京时间 9:00-12:00 与 14:00-18:00；空闲 = 高峰一半
# 每项 (高峰价, 空闲价)：cache_hit 缓存命中输入 / input 缓存未命中输入 / output 输出
PRICING = {
    "deepseek-v4-flash": {
        "cache_hit": (0.10, 0.05),
        "input":     (3.00, 1.50),
        "output":    (9.00, 4.50),
    },
    "deepseek-v4-pro": {
        "cache_hit": (0.30, 0.15),
        "input":     (9.00, 4.50),
        "output":    (27.00, 13.50),
    },
}


# 官方周末全天谷价自 2026-08-23（北京时间）00:00 起生效（epoch 秒）
WEEKEND_VALLEY_FROM_SEC = 1787414400

# 峰谷文案模式（官方 buildGroup1）：默认 / 梁文(liangwen) / 强强(qiangqiang)
PEAK_LABEL = {
    "default":    ("高峰时段", "#e5484d", "空闲时段", "#2fa86c"),
    "liangwen":   ("高峰·梁文不在", "#e5484d", "空闲·梁文摸鱼", "#2fa86c"),
    "qiangqiang": ("高峰·强强发力", "#e5484d", "空闲·强强休息", "#2fa86c"),
}


def is_peak_hour(t=None, ts=None):
    """是否为高峰时段（北京时间 9-12 与 14-18 点）。

    - t: 可传入 time.struct_time（本地时间）
    - ts: 可传入 epoch 秒（用于按北京时间换算出峰值，历史分桶换算用）
    2026-08-23 起周六、日全天按空闲（谷价）。
    """
    if ts is not None:
        lt = time.gmtime(ts + 8 * 3600)   # 北京时间
    else:
        lt = t if t is not None else time.localtime()
    h, wd = lt.tm_hour, lt.tm_wday
    if (ts if ts is not None else time.time()) >= WEEKEND_VALLEY_FROM_SEC and wd >= 5:
        return False
    return (9 <= h < 12) or (14 <= h < 18)


def calc_turn_cost(model, usage):
    """按峰谷定价表换算一轮对话金额（元）。usage 含 prompt_tokens /
    completion_tokens / prompt_cache_hit_tokens；返回 float 或 None。"""
    prices = PRICING.get(model)
    if not prices:
        return None
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    uncached_in = max(0, pt - hit)
    peak = is_peak_hour()
    ci, ii, oi = (0, 0, 0) if peak else (1, 1, 1)
    total = (hit * prices["cache_hit"][ci]
             + uncached_in * prices["input"][ii]
             + ct * prices["output"][oi]) / 1_000_000.0
    return total
