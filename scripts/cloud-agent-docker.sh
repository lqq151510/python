#!/usr/bin/env bash
# 供安装和启动脚本 source。成功时设置 DOCKER 数组。

cloud_agent_install_docker() {
  if command -v dockerd >/dev/null 2>&1 || sudo bash -lc 'command -v dockerd' >/dev/null 2>&1; then
    return 0
  fi
  echo "cloud-agent: installing docker.io"
  if ! sudo apt-get update || ! sudo apt-get install -y docker.io fuse-overlayfs iptables; then
    echo "cloud-agent: docker.io install failed"
    return 0
  fi
  if [[ ! -f /etc/docker/daemon.json ]]; then
    sudo mkdir -p /etc/docker
    printf '%s\n' '{' '  "storage-driver": "fuse-overlayfs"' '}' | sudo tee /etc/docker/daemon.json >/dev/null
  fi
  if [[ -x /usr/sbin/iptables-legacy ]]; then
    sudo update-alternatives --set iptables /usr/sbin/iptables-legacy || true
    sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy || true
  fi
}

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
  if ! docker info >/dev/null 2>&1 && ! sudo docker info >/dev/null 2>&1; then
    if command -v dockerd >/dev/null 2>&1 || sudo bash -lc 'command -v dockerd' >/dev/null 2>&1; then
      echo "cloud-agent: starting dockerd directly" >>/tmp/cloud-agent-docker.log
      sudo dockerd >>/tmp/cloud-agent-docker.log 2>&1 &
    fi
  fi

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
