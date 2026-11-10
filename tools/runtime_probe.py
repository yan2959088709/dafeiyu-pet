# -*- coding: utf-8 -*-
"""运行内核探针：同一个 goal 分别喂给原生 harness / DSH headless / 任意命令。

用法（项目根目录）：
    .venv\\Scripts\\python.exe tools\\runtime_probe.py --runtime dsh \
        --node <node24.exe> --dsh <dsh/lib/bin.js> --home <dsh-home> \
        --goal "Reply with exactly: OK"

    .venv\\Scripts\\python.exe tools\\runtime_probe.py --runtime native \
        --goal "用一句话介绍你自己" --json

密钥只从环境变量读取（默认 DEEPSEEK_API_KEY），不读 config.json、不落盘、
输出统一脱敏。探针只做一次性调用，不拥有记忆/人格/预算。
"""
import argparse
import json
import os
import shlex
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pet import agent_runtime  # noqa: E402


class ProbeWindow:
    """原生 harness 需要的最小窗口替身：无 UI、无真实存储。"""

    def __init__(self, key):
        self.cfg = {"ds_api_key": key, "aiFileEnabled": False, "aiWebEnabled": False,
                    "aiMaxTokens": 256, "city": ""}
        self.facts = []
        self.pet_conf = {}
        self._mem_lock = threading.RLock()

    def _meme_pool(self):
        return {}

    def _add_fact(self, fact):
        self.facts.append(fact)

    def _save_mem(self):
        return None


def _write_events(path, events, secrets):
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            line = json.dumps(event, ensure_ascii=False)
            f.write(agent_runtime.redact(line, secrets) + "\n")


def _print_summary(result, goal, out, secrets, as_json):
    summary = result.to_dict()
    summary["goal"] = goal
    summary["out"] = out
    if as_json:
        print(agent_runtime.redact(json.dumps(summary, ensure_ascii=False, indent=2),
                                   secrets))
        return
    usage = result.usage or {}
    tokens = usage.get("totalTokens") or sum(
        int(usage.get(k) or 0) for k in ("prompt_tokens", "completion_tokens"))
    line = "[%s] exit=%s latency=%sms tokens=%s tool_calls=%s" % (
        result.runtime, result.exit_code, result.latency_ms, tokens,
        result.meta.get("tool_calls", 0))
    print(line)
    if result.error:
        print("error: " + agent_runtime.redact(result.error, secrets))
    if result.text:
        print("text: " + agent_runtime.redact(result.text, secrets)[:500])
    if out:
        print("events: " + out)


def main(argv=None):
    parser = argparse.ArgumentParser(description="AgentRuntime probe")
    parser.add_argument("--runtime", choices=("native", "dsh", "command"), required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--timeout", type=float, default=agent_runtime.DEFAULT_TIMEOUT)
    parser.add_argument("--system", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--node", default="")
    parser.add_argument("--dsh", default="")
    parser.add_argument("--home", default="")
    parser.add_argument("--cwd", default="")
    parser.add_argument("--permission-mode", default="")
    parser.add_argument("--approval-policy", default="")
    parser.add_argument("--command", default="",
                        help="command runtime: argv string, {goal} is substituted")
    args = parser.parse_args(argv)

    key = os.environ.get(args.key_env, "")
    secrets = (key,) if key else ()
    context = {"system": args.system} if args.system else {}

    if args.runtime == "native":
        runtime = agent_runtime.NativeHarnessRuntime(ProbeWindow(key))
    elif args.runtime == "dsh":
        if not args.node or not args.dsh:
            parser.error("--runtime dsh requires --node and --dsh")
        runtime = agent_runtime.DshHeadlessRuntime(
            node_path=args.node, dsh_bin=args.dsh, home=args.home, cwd=args.cwd,
            permission_mode=args.permission_mode,
            approval_policy=args.approval_policy,
            env={args.key_env: key} if key else {},
            secrets=secrets)
    else:
        if not args.command:
            parser.error("--runtime command requires --command")
        runtime = agent_runtime.CommandRuntime(
            shlex.split(args.command), cwd=args.cwd,
            env={args.key_env: key} if key else {}, secrets=secrets)

    result = runtime.run(args.goal, context=context, timeout=args.timeout)
    out = args.out or os.path.join(os.getcwd(), "runtime_probe_%s.jsonl" % result.runtime)
    if result.events:
        _write_events(out, result.events, secrets)
    else:
        out = ""
    _print_summary(result, args.goal, out, secrets, args.json)
    return 0 if result.exit_code == 0 and not result.error else 1


if __name__ == "__main__":
    sys.exit(main())
