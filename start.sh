#!/bin/bash

# 判断 /dev/net/tun 是否存在
if [ -e /dev/net/tun ]; then
  :
else
  echo "/dev/net/tun doesn't exist. Please create it first." >&2
  exit 1
fi

# 定义一个处理函数，用于在接收到 INT 信号时退出脚本
function cleanup {
  echo "Exiting script..."
  exit 0
}

# 捕获 INT 信号，并调用 cleanup 函数
trap cleanup INT

# TopSAP 建立的是全局隧道：会把 0.0.0.0/1 与 128.0.0.0/1 都指向 tun0。
# 这会把「VPN 网关本身」和「DNS 服务器」也一并卷进隧道，导致：
#   1) 隧道自身的加密数据包被塞回隧道形成自环 -> 数据通道不通（tun0 收不到包）
#   2) 容器内 DNS 失效 -> 域名解析不了
# 这里给这两类地址补一条走物理网卡的 /32 路由，把它们排除在隧道之外。
function setup_physical_routes {
  local gw dev host ip dns
  gw=$(ip route show default | awk '{print $3}' | head -1)
  dev=$(ip route show default | awk '{print $5}' | head -1)
  [ -z "$gw" ] && return

  # 1) VPN 网关地址
  host=$(printf '%s' "${SERVER_ADDRESS:-}" | sed -E 's#^[a-zA-Z]+://##; s#[:/].*$##')
  if [ -n "$host" ]; then
    if printf '%s' "$host" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
      ip route replace "$host/32" via "$gw" dev "$dev" 2>/dev/null && \
        echo "[route] $host/32 via $gw dev $dev"
    else
      for ip in $(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1}' | sort -u); do
        ip route replace "$ip/32" via "$gw" dev "$dev" 2>/dev/null && \
          echo "[route] $ip/32 via $gw dev $dev"
      done
    fi
  fi

  # 2) DNS 服务器（忽略 IPv6 地址，容器里到不了）
  for dns in $(awk '/^nameserver/{print $2}' /etc/resolv.conf 2>/dev/null); do
    case "$dns" in
      *:*) continue ;;
    esac
    ip route replace "$dns/32" via "$gw" dev "$dev" 2>/dev/null && \
      echo "[route] $dns/32 via $gw dev $dev"
  done
}

# 启动 TopSAP 的本地服务
cd /opt/TopSAP && ./sv_websrv >/home/work/sv_websrv.log 2>&1 &

# 等待 sv_websrv 就绪
for _ in $(seq 1 30); do
  (echo > /dev/tcp/127.0.0.1/7443) 2>/dev/null && break
  sleep 1
done

# 登录前先把网关/DNS 排除出隧道
setup_physical_routes

# 监听隧道状态：tun0 出现时排除路由、改 MTU、开 danted、加 NAT；tun0 消失时停掉 danted
TUNNEL_UP=0
function watch_tunnel {
  while true; do
    if [ -e /sys/class/net/tun0 ]; then
      if [ "$TUNNEL_UP" = "0" ]; then
        setup_physical_routes
        ip link set dev tun0 mtu 1300
        iptables -t nat -C POSTROUTING -o eth0 -j MASQUERADE 2>/dev/null || \
          iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
        danted -f /etc/danted.conf &
        TUNNEL_UP=1
        echo "[tunnel] up, socks5 已就绪"
      fi
    else
      if [ "$TUNNEL_UP" = "1" ]; then
        killall -q danted 2>/dev/null
        TUNNEL_UP=0
        echo "[tunnel] down"
      fi
    fi
    sleep 2
  done
}
watch_tunnel &
WATCH_PID=$!

if [ "${AUTH_TYPE:-password}" = "sms" ]; then
  # 短信认证：启动 Web 控制台，由用户在网页上获取/提交验证码、断开、查看状态
  echo "[start] 短信认证模式，Web 控制台默认监听 ${WEBUI_PORT:-8080} 端口"
  python3 /home/work/webui.py
else
  # 口令认证：使用 topvpn 自动登录
  sed -i "s/{{server_address}}/$SERVER_ADDRESS/g" expect.exp
  sed -i "s/{{user_name}}/$USER_NAME/g" expect.exp
  sed -i "s/{{password}}/$PASSWORD/g" expect.exp

  expect -f expect.exp

  # 等待隧道建立
  for _ in {1..10}; do
    [ -e /sys/class/net/tun0 ] && break
    sleep 3
  done
  if [ ! -e /sys/class/net/tun0 ]; then
    echo "Device tun0 not found."
    exit 1
  fi
  wait $WATCH_PID
fi
