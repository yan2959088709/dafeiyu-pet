# -*- coding: utf-8 -*-
"""农历数据表：从 JSON 读入「某年的 (月,日) → 公历日期」，注入 time_context。

为什么用数据表而不是算：Python 标准库没有农历，自行推算朔望与闰月容易在
边界日差一天；把「数据来源」交给用户/发布方，程序只负责换算与句式，
错了能一眼定位到那条数据。

文件格式（放在程序目录，名为 lunar.json）：
    {
      "2026": {"1-1": "2026-02-17", "1-15": "2026-03-03", "5-5": "2026-06-19"},
      "2027": {"1-1": "2027-02-06"}
    }
键是农历月-日，值是当年的公历日期；只写需要的节日即可。
元宵（1-15）缺失时会用「正月初一 + 14 天」自动补（同月内恒定）。
"""
import datetime
import json
import os

FILE_NAME = "lunar.json"


def load_table(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    table = {}
    for year, days in (raw or {}).items():
        try:
            year_i = int(year)
        except (TypeError, ValueError):
            continue
        mapping = {}
        for key, value in (days or {}).items():
            try:
                month, day = [int(x) for x in str(key).split("-")]
                mapping[(month, day)] = datetime.date.fromisoformat(str(value))
            except (TypeError, ValueError):
                continue
        if mapping:
            table[year_i] = mapping
    return table


def provider_from_table(table):
    """生成 time_context.set_lunar_provider 需要的 fn(year)。"""
    def provider(year):
        mapping = dict((table or {}).get(int(year), {}))
        cny = mapping.get((1, 1))
        if cny and (1, 15) not in mapping:
            mapping[(1, 15)] = cny + datetime.timedelta(days=14)
        return mapping
    return provider


def load_provider(path=None):
    """文件不存在/解析失败返回 None（调用方据此保持「不猜日期」）。"""
    path = path or os.path.join(os.getcwd(), FILE_NAME)
    if not os.path.exists(path):
        return None
    try:
        return provider_from_table(load_table(path))
    except Exception:
        return None
