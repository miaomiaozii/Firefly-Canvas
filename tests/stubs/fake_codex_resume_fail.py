"""#318 票 B：假 codex CLI —— resume 路径必失败（降级链单测用）。

- 有 "resume" 参数：stderr 报错、exit 2、不输出任何 JSONL（镜像真实
  codex「no rollout found」类失败的进程表现）。
- 无 resume：与 tests/stubs/fake_codex.py --json 同词汇输出（thread_id=fake-first）。
- PROMPT_SLEEP 哨兵：stdin 含该词时睡 3s（busy 拒收并发单测用）。
"""
import json
import sys
import time


def main() -> int:
    args = sys.argv[1:]
    try:
        prompt = sys.stdin.read()
    except Exception:
        prompt = ""
    if "resume" in args:
        sys.stderr.write("Error: no rollout found for resume id\n")
        return 2
    if "PROMPT_SLEEP" in prompt:
        time.sleep(3)
    if "ECHO_MODE" in prompt:  # #318 票 C：回显 prompt（降级重放上下文断言用）
        print(json.dumps({"type": "thread.started", "thread_id": "fake-first"}))
        print(json.dumps({"type": "item.completed", "item": {"id": "i1", "type": "agent_message",
                                                            "text": "ECHO:" + prompt}}))
        print(json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 1}}))
        return 0
    print(json.dumps({"type": "thread.started", "thread_id": "fake-first"}))
    print(json.dumps({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "FAKE SECOND"}}))
    print(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 1}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
