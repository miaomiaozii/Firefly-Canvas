"""#239 确定性假 pi sidecar（真实短命进程，用于超时必杀 / 进程树杀测试）。

镜像真实 pi 的 Windows 形态：``.cmd 包装器 -> python -> （可选）孙进程 sleeper``，
让测试能证明「只杀最顶层 Popen」不够，``taskkill /PID <pid> /T /F`` 能杀干净整棵
进程树且无残留。

行为完全由环境变量门控（纯本地进程，不触网、不碰真实 provider）：
- ``FAKE_PI_PIDFILE``               把本进程 pid 写入该文件（死后断言用）
- ``FAKE_PI_NO_STATE=1``            从不回应 get_state（测 spawn 就绪等待超时）
- ``FAKE_PI_ECHO_STATE=1``          对每次 get_state 都回应（测 run_command 成功路径）
- ``FAKE_PI_EXIT=<n>``              回应 get_state 后以该码退出（n>0 模拟崩溃；默认 0=挂死）
- ``FAKE_PI_GRANDCHILD=1``          派生一个孙进程 sleeper（进程树的一员）
- ``FAKE_PI_GRANDCHILD_PIDFILE``    把孙进程 pid 写入该文件
- ``FAKE_PI_STDERR_LINES=<n>``      先向 stderr 写 n 行 80 字符（测尾部 800 字截断 + 管道背压）

默认行为：回应第一个 get_state，然后无限 sleep（模拟挂死的 pi）。
"""
import json
import os
import subprocess
import sys
import time


def _write(path: str, value: str) -> None:
    if not path:
        return
    try:
        with open(path, "w") as handle:
            handle.write(value)
    except OSError:
        pass


def main() -> None:
    _write(os.environ.get("FAKE_PI_PIDFILE", ""), str(os.getpid()))

    # 先写 stderr：总量超过管道缓冲区时必须靠 agent_host 的 drain 线程取走，
    # 否则本进程会阻塞在 write 上（同时验证 #239 的 stderr 尾截断）。
    stderr_lines = int(os.environ.get("FAKE_PI_STDERR_LINES") or "0")
    for index in range(stderr_lines):
        sys.stderr.write(f"{index:04d}" + "e" * 76 + "\n")
    sys.stderr.flush()

    no_state = os.environ.get("FAKE_PI_NO_STATE") == "1"
    echo_state = os.environ.get("FAKE_PI_ECHO_STATE") == "1"
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            command = json.loads(line)
        except ValueError:
            continue
        if command.get("type") == "get_state" and not no_state:
            print(json.dumps({
                "type": "response",
                "command": "get_state",
                "id": command.get("id"),
                "success": True,
                "data": {"sessionId": "fake-session-239", "sessionFile": "fake-239.session.jsonl"},
            }), flush=True)
            if not echo_state:
                break  # 只答第一次 get_state，随后进入挂死/退出

    exit_code = int(os.environ.get("FAKE_PI_EXIT") or "0")
    if exit_code:
        sys.exit(exit_code)

    if os.environ.get("FAKE_PI_GRANDCHILD") == "1":
        grandchild = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _write(os.environ.get("FAKE_PI_GRANDCHILD_PIDFILE", ""), str(grandchild.pid))

    # 模拟挂死的 pi：吞掉后续命令，无限等待
    time.sleep(600)


if __name__ == "__main__":
    main()
