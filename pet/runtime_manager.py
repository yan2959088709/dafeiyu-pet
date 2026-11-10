# -*- coding: utf-8 -*-
"""重型任务执行内核的配置、预算门禁与 DSH profile patch 生成。

纯逻辑，不碰 Qt。RuntimeGate 只回答「现在能不能跑」，不负责上屏；
上屏与线程封送在 pet/runtime_ui.py。DSH 的 usage 是 camelCase，这里统一
翻译成 pricing/ledger 认识的 snake_case，避免账本和成本各算一套。
"""
import os
import time

from pet import agent_runtime
from pet import drives
from pet.storage import load_json

DEFAULT_RUNTIME = {
    "enabled": False,
    "nodePath": "",
    "dshBin": "",
    "home": "",
    "workspace": "",
    "permissionMode": "workspace-write",
    "dailyBudgetTokens": 50000,
    "maxRunsPerDay": 3,
    "timeoutSec": 300,
}
PERMISSION_MODES = ("workspace-write", "read-only")
RUNS_FILE = "pet_runtime.json"
ESTIMATE_TOKENS = 15000      # DSH trivial 任务实测 ~14.2k；预检按 15k 留余量
TOOL_DISABLE = (
    "tool-bash", "tool-pwsh", "tool-jobs", "tool-todo", "tool-goal",
    "tool-workflow", "tool-subagent", "tool-subagent-control",
    "tool-subagent-fork", "tool-skill",
)
FALLBACK_PERSONA = ("你是桌宠「大肥鱼」的执行内核。只完成主人交代的任务，"
                    "用中文简洁汇报结果；不要扮演聊天角色。")


def normalize_runtime_cfg(seg):
    """缺键回落默认；非法类型保留原值交给 config.validate 纠正。"""
    out = dict(DEFAULT_RUNTIME)
    if isinstance(seg, dict):
        for key, val in seg.items():
            if key in out and val is not None:
                out[key] = val
    return out


def normalize_usage(usage):
    """DSH camelCase / OpenAI snake_case → pricing+ledger 的四个字段。"""
    usage = usage if isinstance(usage, dict) else {}
    if "inputTokens" in usage or "totalTokens" in usage:
        inp = _int(usage.get("inputTokens"))
        cache = _int(usage.get("cacheReadTokens"))
        out = _int(usage.get("outputTokens"))
        total = _int(usage.get("totalTokens")) or (inp + cache + out)
        return {"prompt_tokens": inp + cache, "completion_tokens": out,
                "prompt_cache_hit_tokens": cache, "total_tokens": total}
    prompt = _int(usage.get("prompt_tokens"))
    cache = _int(usage.get("prompt_cache_hit_tokens"))
    out = _int(usage.get("completion_tokens"))
    total = _int(usage.get("total_tokens")) or (prompt + out)
    return {"prompt_tokens": prompt, "completion_tokens": out,
            "prompt_cache_hit_tokens": cache, "total_tokens": total}


def _int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_int(value, default=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _expand(path):
    path = str(path or "").strip()
    if not path:
        return ""
    return os.path.expandvars(os.path.expanduser(path))


class RuntimeGate:
    """日预算 + 24h 次数 + 必填路径的门禁；状态按 pet_id 分文件。"""

    def __init__(self, cfg, pet_id, storage_dir, ledger_loader=None):
        self.cfg = normalize_runtime_cfg(cfg)
        self.storage_dir = str(storage_dir)
        self.runs_path = os.path.join(
            self.storage_dir, drives.state_file_name(RUNS_FILE, pet_id))
        self._ledger_loader = ledger_loader

    def runs_24h(self, now=None):
        now = time.time() if now is None else float(now)
        try:
            raw = load_json(self.runs_path, {"runs": []})
        except Exception:
            return []
        runs = raw.get("runs") if isinstance(raw, dict) else None
        if not isinstance(runs, list):
            return []
        out = []
        for item in runs:
            try:
                ts = float(item)
            except (TypeError, ValueError):
                continue
            if 0.0 <= now - ts < 86400.0:
                out.append(ts)
        return out

    def record_run(self, now=None):
        now = time.time() if now is None else float(now)
        runs = self.runs_24h(now) + [now]
        try:
            drives.atomic_write_json(self.runs_path, {"runs": runs[-100:]})
        except Exception:
            pass

    def today_tokens(self):
        if self._ledger_loader is None:
            return 0
        try:
            data = self._ledger_loader() or {}
            return max(0, int(data.get("today_tokens") or 0))
        except Exception:
            return 0

    def check(self, estimate=ESTIMATE_TOKENS):
        """返回 (ok, reason)。reason 直接给用户看。"""
        if not self.cfg.get("enabled"):
            return False, "执行内核未启用（设置 → 执行内核）"
        if not _expand(self.cfg.get("nodePath")):
            return False, "还没配置 Node 24 路径"
        if not _expand(self.cfg.get("dshBin")):
            return False, "还没配置 DSH CLI 路径"
        today = self.today_tokens()
        budget = _safe_int(self.cfg.get("dailyBudgetTokens"), 0)
        if today + _safe_int(estimate, 0) > budget:
            return False, ("今日预算不足（已用 %d / %d tokens，单次预估 %d）"
                           % (today, budget, estimate))
        runs = self.runs_24h()
        limit = max(1, _safe_int(self.cfg.get("maxRunsPerDay"), 1))
        if len(runs) >= limit:
            return False, "24 小时内已跑满 %d 次重型任务" % len(runs)
        return True, ""


def write_dsh_patch(path, persona=""):
    """生成 --patch 覆盖：宠物人格前缀 + 裁掉 coding/规划类工具。"""
    text = str(persona or "").strip() or FALLBACK_PERSONA
    lines = [
        "# generated by dafeiyu-pet TASK-016",
        "- id: system-prompt",
        "  config:",
        "    personaPrefix: |-",
    ]
    for line in text.splitlines() or [""]:
        lines.append("      " + line)
    for tool_id in TOOL_DISABLE:
        lines.extend(["- id: " + tool_id, "  disabled: true"])
    payload = "\n".join(lines) + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(payload)
    os.replace(tmp, path)
    return path


def build_dsh_runtime(seg, *, key, persona="", app_dir):
    """按配置构建 sidecar；路径为空时落到 app_dir 下的隔离目录。"""
    cfg = normalize_runtime_cfg(seg)
    home = _expand(cfg.get("home")) or os.path.join(app_dir, "dsh-home")
    workspace = _expand(cfg.get("workspace")) or os.path.join(app_dir, "dsh-workspace")
    os.makedirs(home, exist_ok=True)
    os.makedirs(workspace, exist_ok=True)
    patch_path = os.path.join(app_dir, "dsh-runtime.patch.yml")
    write_dsh_patch(patch_path, persona)
    env = {"DEEPSEEK_API_KEY": key} if key else {}
    return agent_runtime.DshHeadlessRuntime(
        node_path=_expand(cfg.get("nodePath")),
        dsh_bin=_expand(cfg.get("dshBin")),
        home=home, cwd=workspace,
        permission_mode=str(cfg.get("permissionMode") or "workspace-write"),
        approval_policy="never",
        launcher_args=["--patch", patch_path],
        env=env, secrets=(key,) if key else (),
    )
