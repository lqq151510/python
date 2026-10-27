#!/usr/bin/env bash
# 供安装和启动脚本 source。成功时设置 DOCKER 数组。

cloud_agent_start_docker() {
  if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
    return 0
  fi
  if sudo docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
    return 0
  fi

  sudo service docker start >/tmp/cloud-agent-docker.log 2>&1 \
    || sudo systemctl start docker >>/tmp/cloud-agent-docker.log 2>&1 \
    || true

  local attempt
  for attempt in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then
      DOCKER=(docker)
      return 0
    fi
    if sudo docker info >/dev/null 2>&1; then
      DOCKER=(sudo docker)
      return 0
    fi
    sleep 1
  done
  return 1
}

cloud_agent_docker_log_tail() {
  if [[ -f /tmp/cloud-agent-docker.log ]]; then
    tail -n 20 /tmp/cloud-agent-docker.log
  fi
}
