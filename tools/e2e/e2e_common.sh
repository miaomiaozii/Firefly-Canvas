#!/usr/bin/env bash
# #360：套件只注册场景；端口、项目、Tabbit 和清理由 Python 入口持有。
# 直接运行套件时拒绝启动，而不是连接任意已运行实例。
set -eu

e2e_start() {
  : "${FIREFLY_E2E_REGISTRY:?请用 python tests/tabbit_e2e_evidence.py 运行隔离套件}"
  printf '%s\n' "$1" "$2" "${3:-25000}" "${E2E_AGENT_NODE:-0}" > "$FIREFLY_E2E_REGISTRY/suite"
  E2E_SCENE_COUNT=0
}

e2e_run() {
  E2E_SCENE_COUNT=$((E2E_SCENE_COUNT + 1))
  [[ "$1" =~ ^[a-z0-9-]+$ ]]
  printf -v path '%s/%03d-%s.js' "$FIREFLY_E2E_REGISTRY" "$E2E_SCENE_COUNT" "$1"
  cat > "$path"
}

e2e_finish() {
  [ "$E2E_SCENE_COUNT" -gt 0 ]
}
