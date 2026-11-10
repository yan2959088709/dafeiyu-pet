# -*- coding: utf-8 -*-
"""可替换执行内核：把「谁来做」从生命核心中拆出去。

LifeCore（drives / intentions / memory / persona / budget）拥有唯一真源；
runtime 只执行一个 goal，返回结构化结果。原生适配器包住既有 harness，
DSH 适配器驱动 `dsh --profile headless --json` 的 NDJSON 事件流。

线程约定：run() 是同步阻塞调用，调用方（chat/heartbeat worker）负责放到
后台线程；本模块不碰 Qt，也不写运行期数据。
"""
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

NATIVE_RUNTIME_ID = "native"
DSH_RUNTIME_ID = "dsh"
DEFAULT_TIMEOUT = 180.0
USAGE_KEYS = ("inputTokens", "outputTokens", "cacheReadTokens",
              "cacheWriteTokens", "totalTokens")


@dataclass
class RuntimeResult:
    runtime: str
    text: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    exit_code: int = 0
    latency_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {
            "runtime": self.runtime,
            "text": self.text,
            "usage": dict(self.usage),
            "error": self.error,
            "exit_code": self.exit_code,
            "latency_ms": self.latency_ms,
            "meta": dict(self.meta),
            "event_count": len(self.events),
        }


class AgentRuntime(Protocol):
    id: str

    def run(self, goal: str, *, context: dict | None = None,
            timeout: float = DEFAULT_TIMEOUT) -> RuntimeResult:
        ...


def redact(text, secrets=()):
    """把已知密钥从任何可能外泄的文本里抹掉（stderr/错误信息）。"""
    out = str(text or "")
    for secret in secrets or ():
        if secret:
            out = out.replace(str(secret), "<redacted>")
    return out


def parse_ndjson(text):
    """容错解析 NDJSON：坏行跳过，非对象跳过，永不抛。"""
    events = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def aggregate_dsh_events(events, exit_code=0, stderr="", secrets=()):
    """把 DSH headless 的 NDJSON 事件折叠成 RuntimeResult。

    usage 只累加 step_end 的用量（turn_end 不带 usage，累加会重复）；
    final.text 优先，缺失时回退最后一条 text；错误优先取 final.reason，
    其次 error 事件，最后退到（脱敏后的）stderr。
    """
    usage = {key: 0 for key in USAGE_KEYS}
    text = ""
    error = ""
    meta = {"event_count": len(events or []), "tool_calls": 0,
            "session_id": "", "cwd": ""}
    for event in events or []:
        kind = event.get("type")
        if kind == "session":
            meta["session_id"] = str(event.get("sessionId") or "")
            meta["cwd"] = str(event.get("cwd") or "")
        elif kind == "status":
            if event.get("phase") == "step_end" and isinstance(event.get("usage"), dict):
                for key in USAGE_KEYS:
                    try:
                        usage[key] += int(event["usage"].get(key) or 0)
                    except (TypeError, ValueError):
                        continue
        elif kind == "tool_call":
            meta["tool_calls"] += 1
        elif kind == "text":
            text = str(event.get("text") or text)
        elif kind == "final":
            text = str(event.get("text") or text)
            reason = event.get("reason") or {}
            if isinstance(reason, dict) and reason.get("kind") not in (None, "completed"):
                detail = reason.get("error")
                if isinstance(detail, dict):
                    error = str(detail.get("message") or detail.get("code") or detail)
                elif detail:
                    error = str(detail)
        elif kind == "error":
            error = str(event.get("message") or error)
    if not error and exit_code != 0:
        error = redact(str(stderr or "").strip()[-500:], secrets) \
            or "runtime exited with code %s" % exit_code
    if not text and not error and exit_code != 0:
        error = "runtime exited with code %s" % exit_code
    return RuntimeResult(runtime=DSH_RUNTIME_ID, text=text, usage=usage,
                         events=list(events or []), error=error,
                         exit_code=int(exit_code or 0), meta=meta)


class NativeHarnessRuntime:
    """现有 harness.run_turn 的薄适配：行为与原路径完全一致。"""

    id = NATIVE_RUNTIME_ID

    def __init__(self, win, *, runner: Callable | None = None):
        self._win = win
        self._runner = runner

    def run(self, goal, *, context=None, timeout=DEFAULT_TIMEOUT):
        started = time.monotonic()
        try:
            if self._runner is None:
                from pet import harness
                runner = harness.run_turn
            else:
                runner = self._runner
            key = (getattr(self._win, "cfg", {}) or {}).get("ds_api_key", "")
            messages = []
            system = (context or {}).get("system")
            if system:
                messages.append({"role": "system", "content": str(system)})
            messages.append({"role": "user", "content": str(goal or "")})
            state = {"meme": None, "sticker": None, "anim": None}
            text, usage, state = runner(self._win, messages, key, state=state)
            return RuntimeResult(
                runtime=self.id, text=str(text or ""),
                usage=dict(usage or {}), events=[],
                latency_ms=int((time.monotonic() - started) * 1000),
                meta={"state": state})
        except Exception as exc:
            return RuntimeResult(
                runtime=self.id, error="%s: %s" % (type(exc).__name__, exc),
                exit_code=1,
                latency_ms=int((time.monotonic() - started) * 1000))


class DshHeadlessRuntime:
    """DSH headless sidecar：一次一个 goal，NDJSON 事件，无端口、无 GUI。"""

    id = DSH_RUNTIME_ID

    def __init__(self, *, node_path, dsh_bin, home="", cwd="",
                 permission_mode="", approval_policy="", extra_args=None,
                 env=None, runner: Callable | None = None, secrets=()):
        self.node_path = str(node_path)
        self.dsh_bin = str(dsh_bin)
        self.home = str(home or "")
        self.cwd = str(cwd or "")
        self.permission_mode = str(permission_mode or "")
        self.approval_policy = str(approval_policy or "")
        self.extra_args = list(extra_args or [])
        self.env = dict(env or {})
        self._runner = runner
        self.secrets = tuple(s for s in (secrets or ()) if s)

    def argv(self, goal):
        argv = [self.node_path, self.dsh_bin, "headless", "--json"]
        if self.permission_mode:
            argv += ["--permission-mode", self.permission_mode]
        if self.approval_policy:
            argv += ["--approval-policy", self.approval_policy]
        argv += self.extra_args
        argv.append(str(goal or ""))
        return argv

    def run(self, goal, *, context=None, timeout=DEFAULT_TIMEOUT):
        started = time.monotonic()
        env = dict(os.environ)
        if self.home:
            env["DSH_HOME"] = self.home
        env.update(self.env)
        runner = self._runner or subprocess.run
        try:
            proc = runner(self.argv(goal), cwd=self.cwd or None, env=env,
                          capture_output=True, text=True, timeout=timeout)
            result = aggregate_dsh_events(parse_ndjson(getattr(proc, "stdout", "")),
                                          getattr(proc, "returncode", 0),
                                          getattr(proc, "stderr", ""),
                                          self.secrets)
            result.latency_ms = int((time.monotonic() - started) * 1000)
            return result
        except subprocess.TimeoutExpired:
            return RuntimeResult(runtime=self.id, error="timeout after %ss" % timeout,
                                 exit_code=124,
                                 latency_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:
            return RuntimeResult(
                runtime=self.id,
                error=redact("%s: %s" % (type(exc).__name__, exc), self.secrets),
                exit_code=1,
                latency_ms=int((time.monotonic() - started) * 1000))


class CommandRuntime:
    """通用 sidecar：把 goal 代入命令模板并收集输出。

    给 OpenClaw 或任意外部 runtime 留的窄缝；parser 可把 stdout 的 JSON
    折叠成 RuntimeResult。argv 里不出现 {goal} 时追加到末尾。
    """

    id = "command"

    def __init__(self, argv, *, cwd="", env=None, runner: Callable | None = None,
                 secrets=(), parser: Callable | None = None, id=None):
        self.argv = [str(a) for a in argv]
        self.cwd = str(cwd or "")
        self.env = dict(env or {})
        self._runner = runner
        self.secrets = tuple(s for s in (secrets or ()) if s)
        self.parser = parser
        if id:
            self.id = str(id)

    def run(self, goal, *, context=None, timeout=DEFAULT_TIMEOUT):
        started = time.monotonic()
        argv = [arg.replace("{goal}", str(goal or "")) for arg in self.argv]
        if goal and all("{goal}" not in arg for arg in self.argv):
            argv.append(str(goal))
        env = dict(os.environ)
        env.update(self.env)
        runner = self._runner or subprocess.run
        try:
            proc = runner(argv, cwd=self.cwd or None, env=env,
                          capture_output=True, text=True, timeout=timeout)
            stdout = getattr(proc, "stdout", "") or ""
            stderr = getattr(proc, "stderr", "") or ""
            code = int(getattr(proc, "returncode", 0) or 0)
            text, usage, events, error, meta = "", {}, [], "", {}
            if self.parser:
                parsed = self.parser(stdout, stderr, code) or {}
                text = str(parsed.get("text") or "")
                usage = parsed.get("usage") or {}
                events = parsed.get("events") or []
                error = str(parsed.get("error") or "")
                meta = parsed.get("meta") or {}
            else:
                text = stdout.strip()
                if code != 0:
                    error = redact(stderr.strip()[-500:], self.secrets) \
                        or "command exited with code %s" % code
            return RuntimeResult(runtime=self.id, text=text, usage=usage,
                                 events=events, error=error, exit_code=code,
                                 latency_ms=int((time.monotonic() - started) * 1000),
                                 meta=meta)
        except subprocess.TimeoutExpired:
            return RuntimeResult(runtime=self.id, error="timeout after %ss" % timeout,
                                 exit_code=124,
                                 latency_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:
            return RuntimeResult(
                runtime=self.id,
                error=redact("%s: %s" % (type(exc).__name__, exc), self.secrets),
                exit_code=1,
                latency_ms=int((time.monotonic() - started) * 1000))
