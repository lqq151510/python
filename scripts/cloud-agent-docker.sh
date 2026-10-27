#!/usr/bin/env bash
# 供安装和启动脚本 source。

cloud_agent_install_docker() {
  if command -v dockerd >/dev/null 2>&1 || sudo bash -lc 'command -v dockerd' >/dev/null 2>&1; then
    return 0
  fi
  echo "cloud-agent: installing docker.io"
  sudo DEBIAN_FRONTEND=noninteractive apt-get update || true
  if ! sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io; then
    echo "cloud-agent: docker.io install failed"
  fi
  if ! sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose || true
  fi
}

cloud_agent_start_docker() {
  if docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1; then
    return 0
  fi

  sudo service docker start >/tmp/cloud-agent-docker.log 2>&1 \
    || sudo systemctl start docker >>/tmp/cloud-agent-docker.log 2>&1 \
    || true
  if ! docker info >/dev/null 2>&1 && ! sudo docker info >/dev/null 2>&1; then
    if command -v dockerd >/dev/null 2>&1 || sudo bash -lc 'command -v dockerd' >/dev/null 2>&1; then
      echo "cloud-agent: starting dockerd directly"
      sudo rm -f /var/run/docker.pid /var/run/docker.sock
      sudo dockerd >>/tmp/cloud-agent-docker.log 2>&1 &
    else
      echo "cloud-agent: dockerd is not installed" >>/tmp/cloud-agent-docker.log
    fi
  fi

  local attempt
  for attempt in $(seq 1 30); do
    if docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

cloud_agent_compose() {
  if sudo docker compose version >/dev/null 2>&1; then
    sudo docker compose "$@"
    return
  fi
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
    return
  fi
  if sudo bash -lc 'command -v docker-compose' >/dev/null 2>&1; then
    sudo docker-compose "$@"
    return
  fi
  echo "cloud-agent: docker compose is not available" >&2
  return 1
}

cloud_agent_stop_docker() {
  if ! sudo docker info >/dev/null 2>&1; then
    return 0
  fi
  sudo pkill -x dockerd >/dev/null 2>&1 || true
  local attempt
  for attempt in $(seq 1 20); do
    if ! sudo docker info >/dev/null 2>&1; then
      sudo rm -f /var/run/docker.pid /var/run/docker.sock
      return 0
    fi
    sleep 1
  done
  return 1
}

cloud_agent_db_image_present() {
  sudo docker image inspect postgres:16-alpine >/dev/null 2>&1 \
    || docker image inspect postgres:16-alpine >/dev/null 2>&1
}

cloud_agent_docker_log_tail() {
  if [[ -f /tmp/cloud-agent-docker.log ]]; then
    tail -n 20 /tmp/cloud-agent-docker.log
  fi
}
