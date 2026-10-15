#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# TopSAP 短信认证 Web 控制台
#
# 有状态的小型 Web 服务：在网页上点「获取验证码 / 提交 / 断开」，并自动刷新连接状态。
# 底层通过本机 sv_websrv 的 HTTP 接口（get_auth_param / send_sms / login_by_sms /
# query_status / logout）完成，与官方客户端走的是同一套接口。
#
# 环境变量：
#   SERVER_ADDRESS  网关地址（必填）
#   USER_NAME       登录用户名（必填）
#   AUTH_TYPE       认证方式（默认 password；sms 时页面显示短信相关按钮）
#   WEBUI_LISTEN    监听地址，默认 0.0.0.0
#   WEBUI_PORT      监听端口，默认 8080
#
import base64
import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SV_HOST = "127.0.0.1"
SV_PORT = 7443
CONTENT_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/65.0.3325.181")

LISTEN_HOST = os.environ.get("WEBUI_LISTEN", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("WEBUI_PORT", "8080"))

# sv_websrv 返回的部分响应是「类 JS 对象」（键名没引号），这里做一次转换
_JS_KEY_RE = re.compile(r'([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:')

# 常见错误码的中文说明
ERR_MSG = {
    "-40014": "用户名或密码为空",
    "-40026": "密码错误",
    "-40039": "需要动态口令/短信验证",
    "-40042": "短信发送失败",
    "-40044": "验证码已超时，请重新获取",
    "-40045": "验证码错误",
    "-40047": "验证码错误",
    "-40074": "该用户没有绑定手机号",
    "-40077": "该账号已在别处登录，请先断开原会话",
    "-40093": "短信认证用户不存在",
    "-40106": "获取用户短信认证信息失败",
}

state = {
    "server": os.environ.get("SERVER_ADDRESS", ""),
    "user": os.environ.get("USER_NAME", ""),
    "auth_type": os.environ.get("AUTH_TYPE", "password"),
    "sms_sent_at": 0.0,
    "last_msg": "",
    "last_error": "",
    "sending": False,
    "logging": False,
}
lock = threading.Lock()
# sv_websrv 不支持并发请求（并发时请求会挂起），所有请求串行化
sv_lock = threading.Lock()
# 会话存活状态，由 query_status 探测更新（tun0 残留不代表会话还活着）
session_state = {"alive": None, "vip": "", "ts": 0.0}


def _sv_request(path, payload, timeout=60):
    """调用本机 sv_websrv 的 HTTP 接口（不持锁）"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection(SV_HOST, SV_PORT, context=ctx, timeout=timeout)
    try:
        conn.request("POST", path, body=body, headers={
            "Content-Type": CONTENT_TYPE,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "User-Agent": USER_AGENT,
        })
        resp = conn.getresponse()
        return resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


def _sv_post(path, payload):
    """调用本机 sv_websrv 的 HTTP 接口（串行化，sv_websrv 不支持并发）"""
    with sv_lock:
        return _sv_request(path, payload)


def _parse_loose(text):
    """解析 sv_websrv 的响应：既支持标准 JSON，也支持类 JS 对象"""
    if not text:
        return None
    text = text.strip()
    if text in ("(null)", "null", "undefined"):
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return json.loads(_JS_KEY_RE.sub(r'\1"\2":', text))
    except Exception:
        return None


def _split_addr(addr):
    """把网关地址拆成 host / port"""
    a = (addr or "").strip()
    for prefix in ("http://", "https://"):
        if a.startswith(prefix):
            a = a[len(prefix):]
    a = a.split("/")[0]
    if a.startswith("[") and "]" in a:          # IPv6 字面量
        host = a[:a.index("]") + 1]
        rest = a[a.index("]") + 1:]
        port = rest[1:] if rest.startswith(":") else "443"
    elif ":" in a:
        host, port = a.rsplit(":", 1)
    else:
        host, port = a, "443"
    return host, (port or "443")


_auth_cache = {"data": None, "ts": 0.0}


def _auth_params(force=False):
    """获取 get_auth_param 结果（缓存 5 分钟，避免网关抖动时反复往返）"""
    now = time.time()
    if not force and _auth_cache["data"] and now - _auth_cache["ts"] < 300:
        return _auth_cache["data"]
    host, port = _split_addr(state["server"])
    payload = {
        "method": "get_auth_param",
        "serverAddr": host,
        "serverPort": port,
        # 这几个 proxy* 字段必须带上，sv_websrv 未做空值判断，缺字段会段错误
        "proxyType": "", "proxyAddr": "", "proxyPort": "",
        "proxyUser": "", "proxyPwd": "", "proxyDomain": "",
        "_flag": 1,
    }
    data = _parse_loose(_sv_post("/api/v1/get_auth_param", payload))
    if not data:
        raise RuntimeError("get_auth_param 无响应，请检查网关地址是否可达")
    if str(data.get("err_code", 0)) not in ("0", "None"):
        raise RuntimeError("get_auth_param 失败：err_code=%s" % data.get("err_code"))
    _auth_cache["data"] = data
    _auth_cache["ts"] = now
    return data


def _clear_auth_cache():
    _auth_cache["data"] = None
    _auth_cache["ts"] = 0.0


def _run_async(flag, fn, *args):
    """在后台线程执行耗时操作（网关可能很慢），立即返回"""
    with lock:
        if state[flag]:
            return False
        state[flag] = True
        state["last_error"] = ""

    def worker():
        try:
            fn(*args)
        except Exception as exc:
            _clear_auth_cache()
            with lock:
                state["last_error"] = str(exc)
        finally:
            with lock:
                state[flag] = False

    threading.Thread(target=worker, daemon=True).start()
    return True


def _probe_session(force=False):
    """通过 query_status 判断会话是否真的活着。

    force=False 时非阻塞获取锁（避免阻塞页面自动轮询）；
    force=True 时等待锁（用于「手动刷新」）。
    """
    if force:
        sv_lock.acquire()
    elif not sv_lock.acquire(blocking=False):
        return
    try:
        d = _parse_loose(
            _sv_request("/api/v1/query_status", {"method": "query_status"}, timeout=6)) or {}
        vip = d.get("vip", "") or ""
        alive = vip not in ("", "0.0.0.0")
        with lock:
            session_state["alive"] = alive
            session_state["vip"] = vip if alive else ""
            session_state["ts"] = time.time()
    except Exception:
        pass
    finally:
        sv_lock.release()


def _sv_ready(timeout=20):
    """等待 sv_websrv 端口就绪"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((SV_HOST, SV_PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _restart_sv():
    """彻底重置 TopSAP 本地服务。

    logout 之后 sv_websrv 会残留旧会话（旧 tun0、卡住的传输连接），
    直接重新登录会拿不到 Virtual_IP；这里 kill 掉重来一遍。
    """
    with sv_lock:
        try:
            subprocess.run(["killall", "-q", "sv_websrv"], timeout=10)
        except Exception:
            pass
        time.sleep(1)
        if os.path.exists("/sys/class/net/tun0"):
            try:
                subprocess.run(["ip", "link", "del", "tun0"], timeout=10,
                               stderr=subprocess.DEVNULL)
            except Exception:
                pass
        try:
            subprocess.Popen("./sv_websrv >> /home/work/sv_websrv.log 2>&1",
                             shell=True, cwd="/opt/TopSAP")
        except Exception:
            pass
    _sv_ready()
    with lock:
        session_state["alive"] = False
        session_state["vip"] = ""


def _login_payload(method, extra=None):
    """按官方客户端的格式构造请求体"""
    data = _auth_params()
    host, port = _split_addr(state["server"])
    user = state["user"]
    b64 = base64.b64encode(user.encode("utf-8")).decode("ascii")
    auth_protocol = data.get("auth_protocol", "0")
    if str(auth_protocol) == "0":
        auth_port = data.get("auth_port") or port
        data_port = data.get("data_port") or port
    else:
        auth_port = data.get("auth_port", "443")
        data_port = data.get("data_port", "443")
    payload = {
        "method": method,
        "vone": {
            "addr": host,
            "port": port,
            "user": urllib.parse.quote(b64, safe=""),
            "U": urllib.parse.quote(user, safe=""),
        },
        "vpn_version": data.get("vpn_version", "sm2"),
        "auth_protocol": auth_protocol,
        "auth_port": auth_port,
        "data_port": data_port,
        "data_protocol": data.get("data_protocol", "0"),
        "cert_type": data.get("cert_type", "0"),
    }
    if extra:
        payload.update(extra)
    return payload


def _err_text(code):
    return ERR_MSG.get(str(code), "错误码 %s" % code)


def send_sms():
    if not state["user"]:
        raise RuntimeError("未设置 USER_NAME")
    if not state["server"]:
        raise RuntimeError("未设置 SERVER_ADDRESS")
    data = _parse_loose(_sv_post("/api/v1/send_sms", _login_payload("send_sms"))) or {}
    ret = data.get("Ret", data.get("err_code"))
    if str(ret) != "0":
        raise RuntimeError("发送短信失败：%s" % _err_text(ret))
    with lock:
        state["sms_sent_at"] = time.time()
        state["last_msg"] = "验证码已发送，请查收短信"
        state["last_error"] = ""
    return data


def login_by_sms(code):
    if not code:
        raise RuntimeError("请输入短信验证码")
    data = _parse_loose(
        _sv_post("/api/v1/login_by_sms", _login_payload("login_by_sms", {"new_sms": code}))
    ) or {}
    err = data.get("err_code")
    if str(err) != "0":
        raise RuntimeError("登录失败：%s" % _err_text(err))
    try:
        _sv_post("/api/v1/get_config_info", {"method": "get_config_info"})
    except Exception:
        pass
    with lock:
        state["last_msg"] = "登录成功，隧道已建立"
        state["last_error"] = ""
    return data


def logout():
    try:
        _sv_post("/api/v1/logout", {"method": "logout"})
    except Exception:
        pass
    # logout 后 sv_websrv 会残留旧会话，彻底重置，否则无法重新登录
    _restart_sv()
    with lock:
        state["last_msg"] = "已断开连接"
        state["last_error"] = ""
    return {}


def status(force=False):
    # 用 query_status 判断会话是否真的活着（tun0 残留不代表会话还在）
    _probe_session(force)
    tun0 = os.path.exists("/sys/class/net/tun0")
    alive = session_state["alive"] is True
    vip = session_state["vip"] if alive else ""
    connected = bool(tun0) and alive
    return {
        "server": state["server"],
        "user": state["user"],
        "auth_type": state["auth_type"],
        "connected": connected,
        "tun0": tun0,
        "vip": vip,
        "sms_sent_at": state["sms_sent_at"],
        "last_msg": state["last_msg"],
        "last_error": state["last_error"],
        "sending": state["sending"],
        "logging": state["logging"],
    }


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TopSAP 控制台</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue",
         "PingFang SC", "Microsoft YaHei", sans-serif; background: #f5f6f8; color: #1f2329;
         display: flex; justify-content: center; padding: 24px 12px; }
  .card { width: 100%; max-width: 460px; background: #fff; border-radius: 14px;
          box-shadow: 0 6px 24px rgba(0,0,0,.08); padding: 22px 22px 26px; }
  h1 { font-size: 19px; margin: 0 0 16px; }
  .row { display: flex; justify-content: space-between; align-items: center;
         padding: 7px 0; font-size: 14px; border-bottom: 1px dashed #eceef1; }
  .row:last-of-type { border-bottom: 0; }
  .k { color: #8a9099; }
  .v { font-weight: 500; word-break: break-all; text-align: right; margin-left: 12px; }
  .badge { padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
  .badge.on { background: #e6f7ec; color: #12833b; }
  .badge.off { background: #f0f1f3; color: #8a9099; }
  .badge.wait { background: #fff4e0; color: #b06f00; }
  .section { margin-top: 18px; }
  .sms-line { display: flex; gap: 8px; }
  input { flex: 1; min-width: 0; padding: 10px 12px; font-size: 15px; border: 1px solid #d7dbe0;
          border-radius: 9px; outline: none; }
  input:focus { border-color: #2f7cf6; }
  button { padding: 10px 16px; font-size: 14px; font-weight: 600; border: 0; border-radius: 9px;
           background: #2f7cf6; color: #fff; cursor: pointer; white-space: nowrap; }
  button:hover { filter: brightness(1.05); }
  button:disabled { opacity: .5; cursor: not-allowed; }
  button.ghost { background: #eef1f5; color: #3a3f47; }
  button.danger { background: #fdecec; color: #d93025; width: 100%; }
  button.mini { padding: 3px 10px; font-size: 12px; font-weight: 500;
                background: #eef1f5; color: #3a3f47; margin-left: 8px; }
  .msg { margin-top: 16px; min-height: 20px; font-size: 13px; line-height: 1.5; color: #6b7280; }
  .msg.ok { color: #12833b; }
  .msg.err { color: #d93025; }
  .hint { margin-top: 10px; font-size: 12px; color: #9aa1ab; }
</style>
</head>
<body>
<div class="card">
  <h1>TopSAP 控制台</h1>
  <div class="row"><span class="k">网关</span><span class="v" id="server">-</span></div>
  <div class="row"><span class="k">用户名</span><span class="v" id="user">-</span></div>
  <div class="row"><span class="k">状态</span><span class="v"><span id="status" class="badge wait">检测中…</span><button id="btnRefresh" class="mini" onclick="manualRefresh()" title="手动刷新状态">刷新</button></span></div>
  <div class="row"><span class="k">隧道 IP</span><span class="v" id="vip">-</span></div>

  <div class="section" id="smsSection">
    <div class="sms-line">
      <button id="btnSend" onclick="sendSms()">获取验证码</button>
      <input id="code" placeholder="短信验证码" inputmode="numeric"
             autocomplete="one-time-code" oninput="updateButtons()"
             onkeydown="if(event.key==='Enter')submitCode()">
      <button id="btnLogin" onclick="submitCode()">提交</button>
    </div>
    <div class="hint">验证码有时效，收到后请尽快提交。</div>
  </div>

  <div class="section">
    <button id="btnLogout" class="danger" onclick="logout()">断开连接</button>
  </div>

  <div class="msg" id="msg"></div>
</div>
<script>
let busy = false;
let cooldown = 0;
let connected = false;
let sending = false;
let logging = false;

function setMsg(t, ok) {
  const m = document.getElementById('msg');
  m.textContent = t || '';
  m.className = 'msg' + (ok === true ? ' ok' : ok === false ? ' err' : '');
}

// 未连接：可「获取验证码 / 提交」，禁用「断开连接」
// 已连接：禁用「获取验证码 / 提交」，可「断开连接」
// 后台正在发送/登录时，对应按钮显示进行中
function updateButtons() {
  const btnSend = document.getElementById('btnSend');
  const btnLogin = document.getElementById('btnLogin');
  const btnLogout = document.getElementById('btnLogout');
  const code = document.getElementById('code');
  if (connected) {
    btnSend.disabled = true;
    btnLogin.disabled = true;
    btnLogout.disabled = busy;
    code.disabled = true;
    btnSend.textContent = '获取验证码';
  } else {
    btnLogout.disabled = true;
    code.disabled = sending;
    btnLogin.disabled = busy || logging || !code.value.trim();
    if (sending) {
      btnSend.disabled = true;
      btnSend.textContent = '发送中…';
    } else if (cooldown > 0) {
      btnSend.disabled = true;
      btnSend.textContent = '重新获取(' + cooldown + ')';
    } else {
      btnSend.disabled = busy;
      btnSend.textContent = '获取验证码';
    }
  }
}

async function refresh(force) {
  try {
    const url = '/api/status' + (force ? '?force=1' : '');
    const d = await (await fetch(url, {cache: 'no-store'})).json();
    document.getElementById('server').textContent = d.server || '-';
    document.getElementById('user').textContent = d.user || '-';
    document.getElementById('vip').textContent = d.vip || '-';
    connected = !!d.connected;
    sending = !!d.sending;
    logging = !!d.logging;
    const s = document.getElementById('status');
    if (connected) { s.textContent = '已连接'; s.className = 'badge on'; }
    else { s.textContent = '未连接'; s.className = 'badge off'; }
    if (d.auth_type !== 'sms') document.getElementById('smsSection').style.display = 'none';
    if (d.last_error) setMsg(d.last_error, false);
    else if (sending) setMsg('正在发送验证码…', null);
    else if (logging) setMsg('正在登录…', null);
    else if (d.last_msg) setMsg(d.last_msg, true);
    updateButtons();
  } catch (e) { /* ignore */ }
}

let refreshing = false;
async function manualRefresh() {
  if (refreshing) return;
  refreshing = true;
  const btn = document.getElementById('btnRefresh');
  btn.disabled = true;
  btn.textContent = '刷新中…';
  await refresh(true);
  btn.disabled = false;
  btn.textContent = '刷新';
  refreshing = false;
}

async function call(path, body) {
  if (busy) return null;
  busy = true;
  updateButtons();
  try {
    const d = await (await fetch(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: body || ''
    })).json();
    if (d.ok) setMsg(d.msg, true); else setMsg(d.error || '操作失败', false);
    return d;
  } catch (e) {
    setMsg('请求失败：' + e, false);
    return null;
  } finally {
    busy = false;
    refresh();
  }
}

function tickCooldown() {
  if (cooldown > 0) cooldown--;
  updateButtons();
}
setInterval(tickCooldown, 1000);

async function sendSms() {
  setMsg('发送中…');
  const d = await call('/api/send_sms');
  if (d && d.ok) { cooldown = 60; updateButtons(); }
}

async function submitCode() {
  const c = document.getElementById('code').value.trim();
  if (!c) { setMsg('请输入验证码', false); return; }
  setMsg('提交中…');
  await call('/api/login', 'code=' + encodeURIComponent(c));
}

async function logout() {
  setMsg('断开中…');
  await call('/api/logout');
}

setInterval(refresh, 2000);
refresh();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path.startswith("/api/status"):
            force = "force=1" in self.path
            self._send(200, json.dumps(status(force=force), ensure_ascii=False))
        else:
            self._send(404, json.dumps({"ok": False, "error": "not found"}))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        try:
            if self.path == "/api/send_sms":
                _run_async("sending", send_sms)
                self._send(200, json.dumps({"ok": True, "msg": "正在发送验证码…"},
                                           ensure_ascii=False))
            elif self.path == "/api/login":
                code = _field(raw, "code").strip()
                if not code:
                    raise RuntimeError("请输入短信验证码")
                _run_async("logging", login_by_sms, code)
                self._send(200, json.dumps({"ok": True, "msg": "正在登录…"},
                                           ensure_ascii=False))
            elif self.path == "/api/logout":
                logout()
                self._send(200, json.dumps({"ok": True, "msg": "已断开连接"},
                                           ensure_ascii=False))
            else:
                self._send(404, json.dumps({"ok": False, "error": "not found"}))
        except Exception as exc:
            _clear_auth_cache()
            with lock:
                state["last_error"] = str(exc)
            self._send(200, json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))


def _field(raw, name):
    """从表单或 JSON body 中取字段"""
    if not raw:
        return ""
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            return str(json.loads(raw).get(name, ""))
        except Exception:
            pass
    return urllib.parse.parse_qs(raw).get(name, [""])[0]


def main():
    print("[webui] TopSAP 控制台已启动： http://%s:%d/  （网关=%s 用户=%s 方式=%s）"
          % (LISTEN_HOST, LISTEN_PORT, state["server"] or "未设置",
             state["user"] or "未设置", state["auth_type"]), flush=True)
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
