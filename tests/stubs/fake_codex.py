"""#258: 假 codex CLI（仿 `codex exec`，供 tests/test_codex_usage.py 使用）。

- 无 --json：stdout 输出纯文本 FAKE PLAIN
- 有 --json：stdout 输出 JSONL（thread.started / item.completed agent_message /
  turn.completed 带 usage，键为 codex 原始风格 input_tokens/output_tokens/cached_input_tokens）
- --output-last-message <path>：写 FAKE LAST 到文件
- 消费 stdin（prompt）避免管道阻塞
"""
import json
import sys


def main() -> int:
    args = sys.argv[1:]
    try:
        sys.stdin.read()
    except Exception:
        pass
    last_path = ''
    for i, arg in enumerate(args):
        if arg == '--output-last-message' and i + 1 < len(args):
            last_path = args[i + 1]
    if last_path:
        with open(last_path, 'w', encoding='utf-8') as f:
            f.write('FAKE LAST')
    if '--json' in args:
        print(json.dumps({"type": "thread.started", "thread_id": "fake-1"}))
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "FAKE LAST"}}))
        print(json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 42, "output_tokens": 7, "cached_input_tokens": 3}}))
    else:
        print('FAKE PLAIN')
    return 0


if __name__ == '__main__':
    sys.exit(main())
