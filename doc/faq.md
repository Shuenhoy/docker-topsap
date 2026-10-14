# 常见问题

## `/dev/net/tun doesn't exist. Please create it first.`

启动容器时缺少创建虚拟网卡所需的参数，请加上：

```bash
--device /dev/net/tun --cap-add NET_ADMIN
```

## `Device tun0 not found.`

- **口令认证**：大概率是没加 `-it`（`expect` 需要伪终端），加上即可；也可能是账号 / 密码 / 网关地址不对。
- **短信认证**：请确认已在 Web 控制台完成登录（页面状态为「已连接」）。

## Socks5 代理不通 / 打不开网页

按顺序排查：

1. 看容器日志里有没有隧道建立、路由排除的痕迹：

   ```bash
   docker logs <容器名> | grep -E "route|tunnel"
   # 期望看到：
   # [route] <VPN网关>/32 via ... dev eth0
   # [route] <DNS服务器>/32 via ... dev eth0
   # [tunnel] up, socks5 已就绪
   ```

2. 确认容器里 `tun0` 已建立：

   ```bash
   docker exec <容器名> ip addr show tun0
   ```

3. 用**只有内网 / VPN 才能访问**的地址验证（外网能直接打开的地址不算数）：

   ```bash
   curl -x socks5h://127.0.0.1:1088 https://内网地址/ -o /dev/null -w "%{http_code}\n"
   ```

4. 如果 `tun0` 收不到包（`cat /sys/class/net/tun0/statistics/rx_bytes` 一直是 0），
   通常是 **VPN 网关 / DNS 服务器被 TopSAP 的全局隧道卷进了 tun0**，导致隧道自环、DNS 失效。
   本项目的镜像已在登录前后自动把这两类地址排除出隧道；若你用的是旧镜像，请拉取最新镜像。

## 短信认证：Web 控制台打不开

- 启动容器时要把端口映射出来：`-p 127.0.0.1:8080:8080`（或用 `-e WEBUI_PORT=...` 自定义）。
- 浏览器访问 <http://127.0.0.1:8080/>。
- 安全起见请只映射到宿主机回环地址，不要暴露到公网。

## 短信认证：验证码超时 / 收不到

- 验证码有时效（一般几分钟），收到后请尽快在页面上提交；超时点「获取验证码」重发。
- 短信由网关下发，网关偶尔会抖动，点「获取验证码」后请稍等几秒到几十秒（按钮会显示「发送中…」）。
- 提示「该账号已在别处登录」时，请先在原设备退出会话。

## 短信认证：网关开启了图形验证码

目前暂不支持图形验证码，需要网关关闭该功能，或改用口令认证。

## 其他问题

请提交 [issue](https://github.com/Shuenhoy/docker-topsap/issues) 反馈。
