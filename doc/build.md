# 构建说明

## 从 Dockerfile 构建

```bash
git clone https://github.com/libra146/docker-topsap
cd docker-topsap
docker build -t libra146/docker-topsap:latest -f Dockerfile .
```

构建时会根据当前架构（`uname -m`）自动选择并安装对应的 deb 包：

- **x86_64**：`TopSAP-3.5.2.36.2-x86_64.deb`
- **aarch64**：`TopSAP-3.5.2.40.2-aarch64.deb`

如果您需要的版本不是这两个，请 clone 项目后替换 deb 包并修改 `Dockerfile` 重新构建。

## 关于版本

> ⚠️ 自 `3.6.3.17.2` 起，官方 x86_64 客户端改成了 **Electron 图形程序**（`/opt/TopSAP/ele/` + `TopSAP.node`），
> **不再提供 `topvpn` 命令行**，现有的 expect 自动登录方式无法复用。因此本项目的**口令认证**停留在仍带命令行的 `3.5.2.36.2`；
> aarch64 目前最高为 `3.5.2.40.2`。**短信认证**走的是 `sv_websrv` 的 HTTP 接口，不受此限制。

因官方版本更新较快、版本较多，无法匹配所有版本，请见谅！

## 多架构

仓库的 GitHub Actions（`.github/workflows/docker-publish.yml`）会构建并推送 `linux/amd64` 与 `linux/arm64` 双架构镜像。本地如需构建多架构，可使用：

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t libra146/docker-topsap:latest .
```
