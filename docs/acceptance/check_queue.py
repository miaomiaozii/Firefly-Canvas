#!/usr/bin/env python3
"""卡点队列状态列回归（`队列状态计数`，P-60）。

用法：python docs/acceptance/check_queue.py [pain.md 路径]
判据：
  1) 逐行取 `| P-xx |` 行的状态列（最后一个非空单元格，忽略转义竖线 `\\|`）；
  2) 状态列首词必须落在状态词表内（棘轮契约 §4：pending / queued / unverified /
     verified / absorbed-vN / merged→P-xx / code-issue），词表外即红（exit 1）；
  3) 按档位打印计数，供 `ROSTER` §0 状态锚照抄——状态锚的队列行必须等于本脚本读数。
退出码：0＝全部在词表内；1＝存在词表外状态（打印 offending 行 ID）。
"""
import io
import re
import sys
from collections import Counter

DEFAULT = "docs/acceptance/pain.md"
VOCAB = re.compile(
    r"^(pending|queued|unverified|verified|absorbed-v\d+|merged\u2192P-\d+|code-issue)$"
)


def status_of(line: str) -> str | None:
    if not re.match(r"^\|\s*P-\d+\s*\|", line):
        return None
    cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip()) if c.strip()]
    return cells[-1] if cells else ""


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    counts: Counter[str] = Counter()
    bad: list[str] = []
    total = 0
    for line in io.open(path, encoding="utf-8"):
        st = status_of(line)
        if st is None:
            continue
        total += 1
        pid = re.match(r"^\|\s*(P-\d+)", line).group(1)
        # 状态列允许在词表词后追加括注（如 `absorbed-v11（落点 …）`）
        head = re.match(r"^[^\s（(]+", st)
        key = head.group(0) if head else st
        if not VOCAB.match(key):
            bad.append(f"{pid}: {st[:80]}")
        counts[key] += 1
    print(f"pain_rows={total}")
    for key in sorted(counts, key=lambda k: (-counts[k], k)):
        print(f"  {key} = {counts[key]}")
    if bad:
        print(f"BAD_STATUS_VOCAB={len(bad)}")
        for b in bad:
            print("  " + b)
        return 1
    print("QUEUE_CHECK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
