# 更新日志

格式基于 [Keep a Changelog](http://keepachangelog.com/)

## Unreleased
### Added
- Web 控制台新增「刷新」按钮，可手动强制刷新连接状态
- 常见错误码映射为中文提示（如 `-40167` 验证码错误次数超限、`-40044` 验证码超时、`-40077` 已在别处登录等）

### Fixed
- 修复连接状态误判：之前只看 `tun0` 是否存在，会话掉线后残留的 `tun0` 会导致仍显示「已连接」；现在通过 `query_status` 判断会话是否真的存活
- 修复断开后无法重新登录：`logout` 后 `sv_websrv` 会残留旧会话（旧 tun0、卡住的传输连接），导致重新登录拿不到 Virtual_IP；现在「断开连接」会彻底重置本地服务

## 0.2.0 - 2026-10-14
### Added
- 支持短信认证（`AUTH_TYPE=sms`）：内置 Web 控制台，可在网页上获取验证码、提交登录、断开连接，并自动刷新连接状态
- 隧道状态自动监听：`tun0` 建立 / 断开时自动启停 Socks5（danted）

### Changed
- 更新 aarch64 版本 TopSAP 到 3.5.2.40.2
- 修复 Debian buster 官方源已下线导致镜像无法构建的问题（改用 archive 源）
- 镜像发布从 Docker Hub 改为 GitHub Container Registry（`ghcr.io/shuenhoy/docker-topsap`）
- 本仓库 fork 自 [libra146/docker-topsap](https://github.com/libra146/docker-topsap)，并适配了相关命名

### Fixed
- 修复 Socks5 代理不通：TopSAP 的全局隧道（`0.0.0.0/1` + `128.0.0.0/1` 指向 tun0）会把 **VPN 网关**和 **DNS 服务器**也卷进隧道，导致隧道加密包自环（数据通道不通）、容器 DNS 失效；现在登录前后都会把这两类地址补一条走物理网卡的 /32 路由排除出去
- 修复 Web 控制台偶发卡死：`sv_websrv` 不支持并发请求，页面状态轮询会与登录/发送请求并发导致其挂起；现在所有请求串行化、状态检测改读本地 `tun0`，且发送/登录改为后台异步执行

## 0.1.2 - 2023-05-30
### Fixed
- 添加 ARM 架构支持


## 0.1.1 - 2023-03-20
### Fixed
- 修复 docker 内无法访问公网的问题
- 更新基础镜像，更新 TopSAP
- 解决无法安装的问题


## 0.1.0 - 2023-03-14
### Added
- 首个版本发布

#### 已知问题：

- 暂无
