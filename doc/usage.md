# 用法

**启动参数里的 `--device /dev/net/tun --cap-add NET_ADMIN` 一定要加。 因为 TopSAP 要使用它来创建虚拟网络设备。**

## 使用方法

1. 安装 Docker。

2. 在终端输入：
   `docker run -d -it --device /dev/net/tun --cap-add NET_ADMIN -p 127.0.0.1:1088:1080 --name some_name -e USER_NAME=you_name -e PASSWORD=you_password -e SERVER_ADDRESS=you_address ghcr.io/shuenhoy/docker-topsap`

3. 将以上命令的环境变量替换成对应的用户名密码还有服务器地址。

4. 浏览器（或其他支持的应用）可配置socks5代理，地址 127.0.0.1, 端口 1080。

5. 注意⚠️：**口令认证**时 docker 参数中的 `-it` 一定要加 —— `expect` 需要在终端中才能正常执行，docker 需要分配一个伪终端给它，否则会卡在输入密码的地方。（**短信认证**用 Web 控制台，不需要 `-it`。）

## 环境变量

- `USER_NAME`：用户名，TopSAP登录用户名

- `PASSWORD`：密码，ToPSAP账号登录密码

- `SERVER_ADDRESS`：服务器地址

- `AUTH_TYPE`：认证方式，`password`（口令认证，默认）或 `sms`（短信认证，使用内置 Web 控制台）

- `WEBUI_PORT`：（可选，仅短信认证）Web 控制台监听端口，默认 `8080`

## 短信认证

如果你们的账号使用「短信认证」（输入用户名 → 点“发送短信” → 输入验证码）登录，项目内置了一个 **Web 控制台**，在浏览器里点几下即可完成登录 / 断开，并自动显示连接状态。

1. 启动容器（额外把 8080 端口映射出来）：

   ```bash
   docker run -d -it --device /dev/net/tun --cap-add NET_ADMIN \
     -p 127.0.0.1:1088:1080 -p 127.0.0.1:8080:8080 --name some_name \
     -e AUTH_TYPE=sms -e USER_NAME=you_name -e SERVER_ADDRESS=you_address \
     ghcr.io/shuenhoy/docker-topsap
   ```

2. 浏览器打开 <http://127.0.0.1:8080/>，在页面上操作：
   - 点 **「获取验证码」** → 手机收到短信验证码；
   - 填入验证码点 **「提交」** → 登录成功、隧道建立，页面状态自动变为「已连接」并显示隧道 IP；
   - 需要时点 **「断开连接」** 即可退出。

   页面每 2 秒自动刷新一次连接状态；隧道建立后会自动开启 Socks5（见下方「服务」）。

3. 登录成功后即可按下方「服务」中的说明使用 Socks5 代理。

> ⚠️ 验证码有时效（一般几分钟），收到后请尽快提交；超时点「获取验证码」重发即可。
>
> 说明：短信认证通过本机 `sv_websrv` 服务的 HTTP 接口（`send_sms` / `login_by_sms`）完成，不依赖 `topvpn` 命令行。
>
> 安全提示：控制台默认监听容器内 `0.0.0.0:8080`，请务必只映射到宿主机回环地址（`-p 127.0.0.1:8080:8080`），不要暴露到公网。
>
> 如果网关开启了图形验证码，目前暂不支持。


## 如果遇到特殊字符，则可以使用文件传递环境变量的内容

1. 安装 Docker。

2. 将服务器地址，账号，密码填写到 .env 文件中对应的位置中。

3. 在终端输入：
   `docker run -d -it --device /dev/net/tun --cap-add NET_ADMIN -p 127.0.0.1:1088:1080 --name some_name --env-file .env ghcr.io/shuenhoy/docker-topsap`

4. 浏览器（或其他支持的应用）可配置socks5代理，地址 127.0.0.1, 端口 1080。

5. 注意⚠️：**口令认证**时 docker 参数中的 `-it` 一定要加 —— `expect` 需要在终端中才能正常执行，docker 需要分配一个伪终端给它，否则会卡在输入密码的地方。（**短信认证**用 Web 控制台，不需要 `-it`。）


## 服务

### Socks5

登录成功后，容器会自动开启 Socks5（`danted`），默认监听容器内的 1080 端口，用 `-p` 把外部端口转发到 1080 即可使用；断开连接后会自动停掉。

验证代理是否正常（把 URL 换成只有内网 / VPN 才能访问的地址）：

```bash
curl -x socks5h://127.0.0.1:1088 https://内网地址/ -o /dev/null -w "%{http_code}\n"
```

> 说明：容器启动时会自动把 **VPN 网关**和 **DNS 服务器**排除出隧道（各补一条走物理网卡的 `/32` 路由）。
> 否则 TopSAP 的全局隧道（`0.0.0.0/1` + `128.0.0.0/1` 指向 tun0）会导致隧道加密包自环、容器 DNS 失效。
> 若代理不通，可先 `docker logs` 看是否有 `[route] ...` 与 `[tunnel] up` 输出。

## 注意❗️

TopSAP 在 docker 内运行时会获取本机的 `/sys/class/dmi/id/product_uuid` 这个文件内的信息作为本机唯一标识，docker 内的 product_uuid 标识默认取的是本机的 `/sys/class/dmi/id/product_uuid` ，如果在多个设备上登录可能会导致账号被禁用（取决于服务端设置），如果你所在的公司无 id 限制，可忽略。

```bash
root@956a2f1417dd:/home/work# cd /opt/TopSAP/
root@956a2f1417dd:/opt/TopSAP# ./sv_websrv 
Failed to create listener: cannot parse address
Starting SSL server on port 127.0.0.1:7443, cert from server.pem, key from server.key
mkdir: cannot create directory ‘/opt/sslvpn’: File exists
VPNSDK:The SDK Version is 3.4.221229.0.2

start....---------------------what
open hard disk device failed.overlay,-1
get the diskid error.
get the cpu id error.
diskId:85bc4d56-4295-422a-5510-9ea5b68cc731lUoYuAncHeNtIaNkAijWn
16_MD5:W�:�E��`G7
MD5:5795ee85a83ae345ee180305f8604737
featureCode:5795ee85a83ae345ee180305f8604737
```

目前暂不清楚 Mac 版和 Linux 版获取了什么信息当作了本机唯一标识。

可以确定的是，docker 中因为其他信息获取失败了，所以会获取  `/sys/class/dmi/id/product_uuid` 这里的信息当作唯一标识，如果需要在多个地方运行 docker 容器，需要使用下面的方法来设置标识的值码，使其在多个地方都相同。

```bash
user:~$ sudo cat /sys/class/dmi/id/product_uuid 
85bc4d56-4295-422a-5510-9ea5b68cc731
user:~$ docker run --rm -it ubuntu:22.10 bash
root@3eedbe1d0994:/# cat /sys/class/dmi/id/product_uuid 
85bc4d56-4295-422a-5510-9ea5b68cc731
root@3eedbe1d0994:/# 
exit
user:~$ echo 111 > uuid
user:~$ cat uuid
111
user:~$ docker run --rm -it -v ./uuid:/sys/class/dmi/id/product_uuid ubuntu:22.10 bash
root@f0a197866f1c:/# cat /sys/class/dmi/id/product_uuid 
111
root@f0a197866f1c:/# 
```

在 docker 运行时，使用 `-v` 命令将本地的 uuid 映射到容器中即可修改容器内的 product_uuid。
