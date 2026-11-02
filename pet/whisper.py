# -*- coding: utf-8 -*-
"""碎碎念调度：周期到点、锁跳过、生成与失败文案。

上游没有「概率门控」——到点就生成，锁生效时跳过且**不推进**上次发言时间，
这样锁一放开下一拍就能正常说，而不是多等一个周期。

生成失败分两类：没有可用模型 / 调用失败；两种都要能上屏，不能静默。
"""
DEFAULT_PERIOD_SEC = 300
MIN_PERIOD_SEC = 60
TIMEOUT_MS = 30000
TEMPERATURE = 1.0

PROMPT = "随便说一句日常碎碎念，一句就好，20 字以内。"
IMAGE_SUFFIX = ("\n配图：%s（%s）。说话要配合画面，但别描述画面本身，"
                "也别提到你在挑图。")
NO_MODEL = "当前对话未配置模型"
LLM_FAILED = "LLM 服务不可用"


def _now_ms():
    return int(__import__("time").time() * 1000)


def normalize_period(sec):
    """周期兜底 300s，且不低于 60s（与上游一致）。"""
    try:
        val = float(sec)
    except (TypeError, ValueError):
        return DEFAULT_PERIOD_SEC
    if val != val or val <= 0:
        return DEFAULT_PERIOD_SEC
    return max(float(MIN_PERIOD_SEC), val)


def whisper_prompt(meme_name=None, meme_desc=None):
    if meme_name and meme_desc:
        return PROMPT + IMAGE_SUFFIX % (meme_name, meme_desc)
    return PROMPT


def log_line(checked, due, not_due, skipped, next_sec):
    detail = "占用锁跳过" if skipped else "无"
    return ("[dsh-pet] 碎碎念调度：检查 %d 只 → 到点 %d、未到点 %d、跳过 %d（%s）；"
            "下一拍 %ds" % (checked, due, not_due, skipped, detail, int(next_sec)))


class WhisperScheduler:
    def __init__(self, clock=None):
        self._clock = clock or _now_ms
        self._last = {}

    def _now(self):
        return int(self._clock())

    def due(self, pet_id, period_sec, now_ms=None):
        now = self._now() if now_ms is None else int(now_ms)
        period = normalize_period(period_sec) * 1000.0
        last = self._last.get(str(pet_id))
        if last is None:
            return True
        return now - last >= period

    def mark(self, pet_id, now_ms=None):
        self._last[str(pet_id)] = self._now() if now_ms is None else int(now_ms)

    def mark_all(self, now_ms=None):
        now = self._now() if now_ms is None else int(now_ms)
        for key in list(self._last):
            self._last[key] = now

    def tick(self, pets, is_locked, generate, period_sec=None, now_ms=None):
        """一拍调度：到点且未锁的宠物逐个生成。

        generate(pet_id) -> (ok, text_or_reason)。锁住的宠物**不推进**时间。
        """
        now = self._now() if now_ms is None else int(now_ms)
        period = normalize_period(period_sec)
        due, not_due, skipped, out = 0, 0, 0, []
        for pet in pets or []:
            pid = pet if isinstance(pet, str) else str(pet.get("id"))
            if is_locked(pid):
                skipped += 1
                continue
            if not self.due(pid, period, now):
                not_due += 1
                continue
            due += 1
            self.mark(pid, now)
            ok, payload = generate(pid)
            out.append({"pet": pid, "ok": ok,
                        "text": payload if ok else None,
                        "reason": None if ok else payload})
        return {"checked": len(pets or []), "due": due, "not_due": not_due,
                "skipped": skipped, "items": out,
                "log": log_line(len(pets or []), due, not_due, skipped, period)}

    def generate_once(self, candidates, call, prompt=None):
        return generate_once(candidates, call, prompt)


def generate_once(candidates, call, prompt=None):
    """按候选链逐个尝试；全失败返回最后原因。"""
    prompt = prompt or PROMPT
    if not candidates:
        return {"ok": False, "reason": NO_MODEL}
    reason = LLM_FAILED
    for provider, model in candidates:
        try:
            text = call(provider, model, prompt)
        except Exception as e:
            reason = str(e) or LLM_FAILED
            continue
        if isinstance(text, str) and text.strip():
            return {"ok": True, "text": text.strip(), "provider": provider,
                    "model": model}
        reason = LLM_FAILED
    return {"ok": False, "reason": reason}
