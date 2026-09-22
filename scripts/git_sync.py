#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""git-sync —— 把本地项目一键同步到 Gitee / GitHub。

三个关键设计
    1. 永不挂起。Git for Windows / PortableGit 的 system gitconfig 里是
       `credential.helper=helper-selector`（内部跑 `git config --system -e` 打开编辑器），
       裸跑 `git push` 会永久卡住。对策：`GIT_CONFIG_SYSTEM=NUL` 跳过 system 级配置
       + `GIT_TERMINAL_PROMPT=0` + `GCM_INTERACTIVE=Never` + `-c credential.helper=`。
       认证走「带令牌的显式推送 URL」且不带 `-u`，令牌不落进 .git/config。
    2. 体检的是暂存区，不是目录。先创建仓库、写好 .gitignore、git add，
       再拿 `git diff --cached` 的清单去算体积和查密钥。这样报告的就是
       「真正会被推上去的东西」——.gitignore 挡住的文件不会误报成大文件或假密钥
       （反之，如果 .gitignore 没挡住，就一定会被查出来）。
    3. 推送结果以远端 refs 为准，不信退出码。`git push` 可能静默成功、也可能在有前次
       失败残留时回一句 `Everything up-to-date`。每次尝试后都用 `git ls-remote` 比 SHA。
       失败按「直连 → 清代理 →（GitHub）清代理+绑 IP」分级重试，权限类错误立即停下。

    交互式回车用默认值（PyCharm 直接运行可用），同时保留完整命令行参数；
    无 TTY 时（智能体调用）绝不等待输入，缺参数直接报错。

用法
    python git_sync.py                       # 交互模式，全程回车即可
    python git_sync.py --yes                 # 全自动（智能体用）
    python git_sync.py --dry-run             # 只体检出计划，不提交不推送
    python git_sync.py --platform both --yes # 同时推 Gitee 和 GitHub
    python git_sync.py --set-token gitee=xxx # 存令牌
    python git_sync.py --show-config
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

VERSION = "1.8.0"

CONFIG_PATH = Path.home() / ".workbuddy" / "git-sync.json"

PLATFORMS = ("gitee", "github")
HOSTS = {"gitee": "gitee.com", "github": "github.com"}

# 两个平台的令牌权限口径完全不同，提示文案必须跟着平台走
TOKEN_SCOPE_HINT = {
    "gitee": "Gitee 私人令牌需要勾选 projects 权限（设置 → 私人令牌）。",
    "github": "GitHub 经典令牌需要勾选 repo；细粒度令牌需要 "
              "「Administration: 写」+「Contents: 写」。",
}

# Gitee 个人版配额：单文件 <= 50 MB、单仓库 <= 500 MB
# GitHub：单文件硬限 100 MiB（≈105 MB，超过直接拒收）
# 一律按十进制 MB 计算，和平台公告口径保持一致
SINGLE_WARN = 45 * 1000 * 1000
SINGLE_BLOCK = {"gitee": 50 * 1000 * 1000, "github": 105 * 1000 * 1000}
REPO_WARN = 450 * 1000 * 1000

NULL_DEVICE = "NUL" if os.name == "nt" else "/dev/null"

PROXY_VARS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY")

# 网络操作的环境（本机实测 + 与 github-workflow 技能交叉验证）：
#   - PortableGit 的系统 gitconfig 里是 credential.helper=helper-selector，
#     它内部会跑 `git config --system -e` 打开编辑器，无人交互就永久挂起。
#     `GIT_CONFIG_SYSTEM=NUL` 直接跳过 system 级配置，从根上解决。
#   - 环境里的代理变量会把 git 引向 127.0.0.1 代理，表现为
#     `schannel: failed to receive handshake`。注意 http.curloptResolve 只改 DNS、
#     绕不开代理，所以失败重试时必须先把代理变量清掉。
NO_PROMPT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "Never",
    "GIT_CONFIG_SYSTEM": NULL_DEVICE,
    "GIT_SSH_COMMAND": ("ssh -o BatchMode=yes "
                        "-o StrictHostKeyChecking=accept-new "
                        "-o ConnectTimeout=15"),
}

# GitHub 才做 IP 兜底。DNS 污染在 GitHub 高发，且 IP 会逐个失效、每次要实测；
# Gitee / Codeup / 自建 GitLab 没有权威固定 IP 段，硬绑 IP 反而出错，一律走 DNS。
#
# ⚠️ 这几个只是**最后**的兜底（本机实测过的老段），优先级低于 DNS。
# 别再拿它当第一选择：2026-09-23 实测本机 DNS 解析到 20.205.243.166（亚太段），
# 而写死的这几个是美国段 140.82.11x.4，绑上去只会更慢更不通。见 github_ips()。
GITHUB_FALLBACK_IPS = ("140.82.114.4", "140.82.116.4", "140.82.112.4")


def github_ips(limit=2):
    """github.com 的候选 IP：**系统 DNS 给什么就用什么**，拿不到才退回写死的备用段。

    顺序很要紧。「绑 IP」这一级的目的是绕开 DNS 污染，所以正常情况下
    DNS 解析出的地址就是最优解；写死的老段（美国)在本机反而更慢更不通——
    实测 2026-09-23：DNS → 20.205.243.166（亚太），写死 → 140.82.11x.4。
    所以 DNS 一旦给出结果，就**不再**叠上备用段去白等一轮超时。
    """
    dns_ips = []
    try:
        for info in socket.getaddrinfo("github.com", 443, type=socket.SOCK_STREAM):
            ip = info[4][0]
            if ip not in dns_ips:
                dns_ips.append(ip)
    except OSError:
        pass
    if dns_ips:
        return dns_ips[:limit]
    return list(GITHUB_FALLBACK_IPS[:limit])

NET_ERROR_HINTS = ("schannel", "handshake", "proxy", "timed out", "timeout",
                   "could not resolve", "unable to access", "failed to connect",
                   "server closed abruptly", "connection reset", "connection",
                   "network is unreachable", "empty reply")

# 单次推送超时 / 全部重试的总时长上限（避免网络不通时拖太久）
PUSH_ATTEMPT_TIMEOUT = 150
PUSH_TOTAL_BUDGET = 300


def net_env(strip_proxy=False):
    env = dict(NO_PROMPT_ENV)
    if strip_proxy:
        for var in PROXY_VARS:
            env[var] = None  # None = 从环境里删掉
    return env


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def clean_output(text):
    """git/远端会带 ANSI 颜色码（Gitee 的 remote: 报错就是），清掉再展示。"""
    return ANSI_RE.sub("", text or "").strip()


def is_network_error(text):
    low = (text or "").lower()
    return any(hint in low for hint in NET_ERROR_HINTS)


def git_prefix(host, ip=None, http1=False):
    """构造 git 命令前缀。

    http1=True 时强制 HTTP/1.1。这不是可有可无的开关——2026-09-23 实测：
    本机到 github.com 的 HTTP/2 连接被中途 RST（`Recv failure: Connection was reset`），
    改 HTTP/1.1 后同一个地址立刻能握手成功；而 api.github.com 走 HTTP/2 一直正常。
    所以「HTTP/1.1」是链路被干扰时的一级有效兜底，不是性能调优。
    """
    args = ["git", "-c", "credential.helper="]
    if ip:
        args += ["-c", f"http.curloptResolve={host}:443:{ip}"]
    if http1:
        args += ["-c", "http.version=HTTP/1.1"]
    return args

WALK_SKIP_DIRS = {".git", "node_modules", "site-packages", ".venv", "venv"}

GITIGNORE_TEMPLATE = """\
# ---- Python ----
__pycache__/
*.py[cod]
*.egg-info/
.eggs/

# ---- 虚拟环境 / 环境变量 ----
.venv/
venv/
env/
.env
.env.*
!.env.example
!.env.template

# ---- 模型权重（体积大，走 Release 附件而不是塞进仓库）----
*.pt
*.pth
*.onnx
*.ckpt
*.safetensors
*.h5
*.pb
*.engine
*.trt

# ---- 数据集与运行产物 ----
data/
datasets/
outputs/
runs/
weights/
*.mp4
*.avi

# ---- IDE / 系统 ----
.idea/
.vscode/
*.swp
.DS_Store
Thumbs.db

# ---- 日志 / 缓存 ----
*.log
.cache/
"""

IGNORED_DIR_PROBES = ("data", "datasets", "outputs", "runs", "weights",
                      "dist", "build", ".venv", "venv", "node_modules", "wandb")

SECRET_FILENAME_PATTERNS = [
    (r"^\.env(\.(?!example|sample|template|dist)[\w.-]+)?$", ".env 环境变量文件"),
    (r"\.(pem|key|p12|pfx|jks|keystore|ppk|ovpn)$", "私钥/证书"),
    (r"^id_(rsa|dsa|ecdsa|ed25519)$", "SSH 私钥"),
    (r"^\.?(npmrc|pypirc|netrc|htpasswd)$", "凭据配置"),
    (r"^(credentials|secrets|service-account)(\.\w+)?$", "凭据文件"),
    (r"(^|[_-])(token|secret|apikey|api_key)s?\.(json|txt|yaml|yml|ini)$", "令牌文件"),
]

SECRET_CONTENT_PATTERNS = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "PEM 私钥"),
    (r"\bgh[pousr]_[A-Za-z0-9]{20,}", "GitHub Token"),
    (r"\bsk-[A-Za-z0-9]{24,}", "OpenAI 风格密钥"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS Access Key"),
    (r"\bglpat-[A-Za-z0-9_\-]{20,}", "GitLab Token"),
    (r"(?i)\b(access_token|api[_-]?key|apikey|secret[_-]?key|client[_-]?secret"
     r"|password|passwd)\s*[:=]\s*[\"'][^\"'\s]{16,}[\"']", "硬编码密钥"),
]

CONTENT_SCAN_MAX_BYTES = 256 * 1000
CONTENT_SCAN_MAX_FILES = 4000


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
def _setup_console():
    enc = getattr(sys.stdout, "encoding", "") or "utf-8"
    try:
        "→ 推荐".encode(enc)
    except Exception:
        enc = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding=enc, errors="replace")
        except Exception:
            pass


def out(msg=""):
    print(msg, flush=True)


def head(title):
    out()
    out("─" * 58)
    out(f"  {title}")
    out("─" * 58)


def ok(msg):
    out(f"  [OK]   {msg}")


def info(msg):
    out(f"         {msg}")


def warn(msg):
    out(f"  [注意] {msg}")


def bad(msg):
    out(f"  [出错] {msg}")


def die(msg, code=1):
    out()
    bad(msg)
    sys.exit(code)


def human(nbytes):
    """十进制单位，和 Gitee/GitHub 公告的配额口径一致。"""
    n = float(nbytes)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000


def mask(token):
    if not token:
        return "(未设置)"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}{'*' * 6}{token[-4:]}"


def slugify(name):
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip())
    s = re.sub(r"-{2,}", "-", s).strip("-._")
    return s


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "identity": {"name": "", "email": ""},
    "tokens": {"gitee": "", "github": ""},
    "logins": {"gitee": "", "github": ""},
    "defaults": {"platforms": ["gitee"], "private": True,
                 "proto": "https", "branch": "main"},
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            die(f"配置文件读取失败：{CONFIG_PATH}\n         {exc}")
        for section in ("identity", "tokens", "logins", "defaults"):
            if isinstance(raw.get(section), dict):
                cfg[section].update(raw[section])
    return cfg


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass


# --------------------------------------------------------------------------
# 命令执行
# --------------------------------------------------------------------------
def run(args, cwd=None, extra_env=None, stdin_text=None, timeout=900, check=False):
    env = os.environ.copy()
    for key, value in (extra_env or {}).items():
        if value is None:
            env.pop(key, None)   # None 表示「从环境里删掉这个变量」
        else:
            env[key] = value
    try:
        proc = subprocess.run(
            args, cwd=cwd, env=env, input=stdin_text, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except FileNotFoundError:
        die(f"找不到命令：{args[0]}")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"超时（{timeout} 秒）")
    if check and proc.returncode != 0:
        die(f"命令失败：{' '.join(args)}\n         "
            f"{(proc.stderr or proc.stdout).strip()}")
    return proc


def git(args, cwd=None, extra_env=None, stdin_text=None, timeout=900, check=False):
    return run(["git"] + list(args), cwd=cwd, extra_env=extra_env,
               stdin_text=stdin_text, timeout=timeout, check=check)


def git_out(args, cwd):
    return (git(args, cwd=cwd).stdout or "").strip()


# --------------------------------------------------------------------------
# 平台 API
# --------------------------------------------------------------------------
def http_json(url, method="GET", body=None, form=None, token=None):
    hdrs = {"User-Agent": f"git-sync/{VERSION}", "Accept": "application/json"}
    if token and "github.com" in url:
        hdrs["Authorization"] = f"Bearer {token}"
        hdrs["X-GitHub-Api-Version"] = "2022-11-28"

    data = None
    if form is not None:
        payload = {k: v for k, v in form.items() if v is not None}
        payload = {k: ("true" if v is True else "false" if v is False else str(v))
                   for k, v in payload.items()}
        data = urlparse.urlencode(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json;charset=UTF-8"

    req = urlrequest.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, (json.loads(text) if text else {}), text
            except json.JSONDecodeError:
                return resp.status, {}, text
    except urlerror.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, (json.loads(text) if text else {}), text
        except json.JSONDecodeError:
            return exc.code, {}, text
    except Exception as exc:
        return 0, {}, f"{type(exc).__name__}: {exc}"


def flatten_error(payload, raw):
    """Gitee 的错误体是嵌套的 {"error": {"base": ["..."]}}，GitHub 是 {"message": "..."}"""
    def walk(node, acc):
        if isinstance(node, str):
            acc.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item, acc)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value, acc)

    acc = []
    if isinstance(payload, dict):
        for key in ("error", "message", "errors", "error_description"):
            if key in payload:
                walk(payload[key], acc)
    if not acc and raw:
        acc.append(raw.strip()[:300])
    seen, uniq = set(), []
    for item in acc:
        if item and item not in seen:
            seen.add(item)
            uniq.append(item)
    return " / ".join(uniq) or "未知错误"


def api_login(platform, token):
    if platform == "gitee":
        status, payload, raw = http_json(
            "https://gitee.com/api/v5/user", form={"access_token": token})
    else:
        status, payload, raw = http_json("https://api.github.com/user", token=token)
    if status == 200 and isinstance(payload, dict) and payload.get("login"):
        return payload["login"], ""
    return "", flatten_error(payload, raw)


def api_create_repo(platform, token, name, private, description):
    if platform == "gitee":
        status, payload, raw = http_json(
            "https://gitee.com/api/v5/user/repos", method="POST",
            form={"access_token": token, "name": name, "path": name,
                  "private": private, "auto_init": False,
                  "description": description or ""})
        message = flatten_error(payload, raw)
        already = ("已存在" in message) or ("already exist" in message.lower())
    else:
        status, payload, raw = http_json(
            "https://api.github.com/user/repos", method="POST", token=token,
            body={"name": name, "private": bool(private), "auto_init": False,
                  "description": description or ""})
        message = flatten_error(payload, raw)
        already = status == 422 or "already exists" in message.lower()

    if status in (200, 201):
        return True, True, ""
    if already:
        return True, False, ""
    return False, False, f"HTTP {status}: {message}"


def create_error_hint(err):
    """把建库失败的原话翻译成「你该去哪里点哪个按钮」。

    Gitee 的实名认证门槛实测返回：
      422 {"error":{"base":["当前账户尚未认证身份，请通过「个人设置 - 帐号信息」下完成身份认证后再操作"]}}
    """
    low = (err or "").lower()
    if "认证身份" in err or "身份认证" in err or "实名" in err:
        return ["提示：Gitee 要求账号先完成实名认证才能创建仓库（硬门槛，无 API 可绕）。",
                "      到 Gitee「个人设置 → 帐号信息」完成认证，再重跑本命令即可。"]
    if "projects" in low or "权限" in err or "scope" in low:
        return ["提示：令牌权限不足。Gitee 需勾 projects；GitHub 经典令牌需勾 repo，",
                "      细粒度令牌需「Administration: 写」+「Contents: 写」。"]
    if "401" in err or "unauthorized" in low or "bad credentials" in low:
        return ["提示：令牌无效或已过期。到平台「私人令牌」重新生成（只显示一次）。"]
    if "限制" in err or "403" in err:
        return ["提示：账号被平台限制，或令牌无写权限。"]
    return []


def remote_url(platform, login, repo, proto):
    host = HOSTS[platform]
    if proto == "ssh":
        return f"git@{host}:{login}/{repo}.git"
    return f"https://{host}/{login}/{repo}.git"


def auth_url(platform, login, repo, token):
    host = HOSTS[platform]
    return (f"https://{urlparse.quote(login, safe='')}:"
            f"{urlparse.quote(token, safe='')}@{host}/{login}/{repo}.git")


def web_url(platform, login, repo):
    return f"https://{HOSTS[platform]}/{login}/{repo}"


# --------------------------------------------------------------------------
# 交互
# --------------------------------------------------------------------------
INTERACTIVE = sys.stdin.isatty() and sys.stdout.isatty()


def ask(prompt, default="", yes_mode=False):
    if yes_mode or not INTERACTIVE:
        return default
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        out()
        die("已取消")
    return answer or default


def ask_bool(prompt, default=True, yes_mode=False):
    if yes_mode or not INTERACTIVE:
        return default
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            answer = input(f"  {prompt} [{hint}]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            out()
            die("已取消")
        if not answer:
            return default
        if answer in ("y", "yes", "是"):
            return True
        if answer in ("n", "no", "否"):
            return False
        warn("请输入 y 或 n")


def ask_secret(prompt, default=""):
    """读令牌时不回显。终端不支持时退回明文 input。"""
    if not INTERACTIVE:
        return default
    import getpass
    import warnings
    suffix = " [回车复用已保存的]" if default else ""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            answer = getpass.getpass(f"  {prompt}{suffix}: ")
    except (EOFError, KeyboardInterrupt):
        out()
        die("已取消")
    except Exception:
        try:
            answer = input(f"  {prompt}{suffix}: ")
        except (EOFError, KeyboardInterrupt):
            out()
            die("已取消")
    return answer.strip() or default


def parse_account(text):
    """把用户给的账号地址解析成 (platform, login, repo, err)。

    认得这几种写法：
        https://gitee.com/zhangsan/          -> gitee / zhangsan
        https://gitee.com/zhangsan/proj.git  -> gitee / zhangsan / proj
        git@gitee.com:zhangsan/proj.git      -> 同上（SSH 形式）
        github.com/bob                         -> github / bob
        zhangsan                             -> (None) / zhangsan，平台待定
    """
    t = (text or "").strip()
    if not t:
        return None, None, None, "内容为空"

    platform = None
    path = ""
    ssh = re.match(r"^git@([^:]+):(.+)$", t)
    if ssh:
        host, path = ssh.group(1).lower(), ssh.group(2)
    else:
        # 补协议：允许直接贴 gitee.com/xxx
        if "://" not in t and re.match(r"^(www\.)?(gitee|github)\.com\b", t, re.I):
            t = "https://" + t
        if "://" in t:
            parts = urlparse.urlsplit(t)
            host = (parts.netloc or "").lower()
            path = parts.path or ""
        else:
            # 只给了用户名，平台留空等调用方决定
            return None, t.strip("/").split("/")[0], None, ""

    host = re.sub(r"^www\.", "", host.split(":")[0])
    if host.endswith("gitee.com"):
        platform = "gitee"
    elif host.endswith("github.com"):
        platform = "github"
    else:
        return None, None, None, f"不认识的站点 {host!r}（只支持 gitee.com / github.com）"

    segs = [s for s in path.strip("/").split("/") if s]
    login = segs[0] if segs else ""
    repo = segs[1] if len(segs) > 1 else ""
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not repo and login.endswith(".git") and len(login) > 4:
        # SSH 简写 git@host:用户名.git —— 这一段其实是账号名，别把 .git 当用户名
        login = login[:-4]
    if not login:
        return platform, None, None, "地址里只有域名，没有账号名"
    return platform, login, (repo or None), ""


def account_display(platform, login):
    return f"https://{HOSTS[platform]}/{login}/"


MAX_ASK_RETRY = 5


def login_mismatch(expect_login, actual_login):
    """地址里写的账号 与 令牌实际所属账号 是否冲突（忽略大小写）。"""
    if not expect_login or not actual_login:
        return False
    return expect_login.strip().lower() != actual_login.strip().lower()


def looks_like_account_url(text):
    """判断用户是不是把「账号地址」错填进了「令牌」那一问。

    令牌本身不含 :// ，也不会以 git@ 开头，所以这两个特征足够。
    """
    t = (text or "").strip()
    if not t:
        return False
    if "://" in t or t.lower().startswith("git@"):
        return True
    return bool(re.match(r"^(www\.)?(gitee|github)\.com\b", t, re.I))


def is_first_run(cfg):
    """没存过令牌、没存过账号、没存过提交身份 => 认定是第一次使用。"""
    tokens_empty = not any((cfg.get("tokens") or {}).values())
    logins_empty = not any((cfg.get("logins") or {}).values())
    ident = cfg.get("identity") or {}
    ident_empty = not (ident.get("name") or ident.get("email"))
    return tokens_empty and logins_empty and ident_empty


def print_intro():
    """首次使用时讲清楚：我要做什么、需要你给什么。"""
    head("先花 30 秒说清楚这件事")
    out("  我能做什么：把这个文件夹变成一个 Gitee / GitHub 仓库，然后推上去。")
    info("建仓库、写 .gitignore、提交、推送，都我来——你不用先去网页建一个空库。")
    info("推送前我会先体检：有没有疑似密钥、有没有超限的大文件，有问题先说清楚再动手。")
    out()
    out("  我需要你提供 4 样东西：")
    info("① 账号主页地址   例如 https://gitee.com/你的用户名/")
    info("② 私人令牌       不是登录密码！Gitee 在「设置 → 私人令牌」勾上 projects；")
    info("                 GitHub 在「Settings → Developer settings → Personal access tokens」")
    info("③ 要上传的文件夹  直接粘路径；回车 = 当前目录")
    info("④ 仓库叫什么     回车 = 用文件夹名（中文目录名建议手输一个英文名）")
    out()
    out("  三件你可以放心的：")
    info("· 令牌只留在本机 ~/.workbuddy/git-sync.json，不进仓库、不回显、不写进 .git/config")
    info("· 默认建私有仓库，要公开得你明确点头")
    info("· 你给的账号地址会和令牌实际所属的账号核对，对不上就停下——绝不闷头建到别人名下")
    out()
    info("中途 Ctrl+C 可以退出：提交和推送都在最后一步才开始，不会留下半截操作。")
    out()
    out("  一句话版：把 4 样东西给我，剩下的交给我。")


def wizard_collect(args, cfg, expect_logins=None, hint_repo=None):
    """四问向导：① 账号 ② 令牌 ③ 文件夹 ④ 仓库名，然后交给主流程自动推。

    已经通过命令行给过的项（--account / --dir / --name / --private）不再重复问。
    自己不依赖调用方预解析 --account（早先版本会因此崩）。
    expect_logins 是 {平台: 账号名} —— 按平台分开，两个平台账号名不一样时才不会互相打架。
    """
    expect_logins = dict(expect_logins or {})
    if is_first_run(cfg):
        print_intro()
    head("准备（回车 = 采用方括号里的默认值）")

    # --account 由本函数自行解析，避免调用方漏传 expect_logins
    acct_platform = acct_login = None
    if args.account:
        raws = args.account if isinstance(args.account, list) else [args.account]
        for raw in raws:
            p, lg, rp, err = parse_account(raw)
            if not p or not lg:
                die(f"--account 解析失败：{err or '看不出用户名'}")
            expect_logins.setdefault(p, lg)
            hint_repo = hint_repo or rp
            # 给了多个地址时，优先取与 --platform 对得上的那个当默认值
            if acct_platform is None or (args.platform
                                         and p == args.platform.strip().lower()):
                acct_platform, acct_login = p, lg

    # ---- ① 账号 ----
    platform = args.platform or acct_platform
    login_hint = expect_logins.get(platform or "") or acct_login
    if not login_hint:
        remembered = ""
        for p in (cfg["defaults"].get("platforms") or ("gitee",)):
            if p in HOSTS and cfg["logins"].get(p):
                remembered = p
                break
        default_acct = (account_display(remembered, cfg["logins"][remembered])
                        if remembered else "")
        tries = 0
        while True:
            if tries >= MAX_ASK_RETRY:
                die("没能问出账号地址。也可以直接给：--account https://gitee.com/你的用户名/")
            tries += 1
            text = ask("① 传到哪个账号？贴主页地址，如 https://gitee.com/你的用户名/",
                       default_acct, False)
            p, lg, rp, err = parse_account(text)
            if not lg:
                bad(f"没认出账号：{err or '看不出用户名'}")
                info("可以贴：https://gitee.com/用户名/ · git@gitee.com:用户名/仓库.git · 用户名")
                default_acct = ""
                continue
            if not p:
                # 只给了用户名，平台无法推断 —— 显式问，别默默替你选
                p = (ask("   哪个平台？gitee / github", remembered or "gitee", False)
                     or "").strip().lower()
                if p not in PLATFORMS:
                    bad("平台只能是 gitee 或 github")
                    default_acct = ""
                    continue
            platform, login_hint = p, lg
            if rp and not hint_repo:
                hint_repo = rp
            break
    if not platform:
        platform = "gitee"
    ok(f"账号：{account_display(platform, login_hint)}")

    # ---- ② 令牌 ----
    token = (cfg["tokens"].get(platform)
             or os.environ.get(f"{platform.upper()}_TOKEN", ""))
    if token:
        info(f"{platform} 已存过令牌，直接回车即复用")
    elif not INTERACTIVE:
        die(f"没有 {platform} 令牌：请先 --set-token {platform}=你的私人令牌")
    tries = 0
    while not token:
        if tries >= MAX_ASK_RETRY:
            die(f"{platform} 令牌连续 {MAX_ASK_RETRY} 次没拿到。\n"
                f"         可以先用命令行存好：--set-token {platform}=你的私人令牌")
        tries += 1
        got = ask_secret(f"② {platform} 私人令牌（输入不回显）", "")
        if got and looks_like_account_url(got):
            bad("这看起来是账号地址，不是令牌——第①问答过的那个不用再填一次")
            info("令牌在平台上生成：Gitee「设置 → 私人令牌」／GitHub「Settings → Developer settings」")
            continue
        if got:
            token = got
            cfg["tokens"][platform] = token
            save_config(cfg)
        if not token:
            bad("令牌不能为空")
    if args.dry_run:
        info("演练模式：不联网校验令牌")
    else:
        real, err = api_login(platform, token)
        if real:
            ok(f"令牌有效，属于账号 {real}")
            cfg["logins"][platform] = real
            save_config(cfg)
        else:
            warn(f"令牌校验没通过：{err}")
            info("不拦住你，但多半是权限没勾对（Gitee 需要 projects 权限）")

    # ---- ③ 文件夹 ----
    if args.dir is not None:
        root = Path(args.dir).expanduser()
    else:
        default_dir = os.getcwd()
        root = None
        tries = 0
        while root is None:
            if tries >= MAX_ASK_RETRY:
                die("没能问出有效目录。可以直接给：--dir <文件夹路径>")
            tries += 1
            text = ask("③ 要上传哪个文件夹？", default_dir, False)
            cand = Path(text).expanduser()
            if cand.is_dir():
                root = cand
            else:
                bad(f"目录不存在：{cand}")
                default_dir = os.getcwd()
    root = root.resolve()
    ok(f"文件夹：{root}")

    # ---- ④ 仓库名 ----
    default_name = slugify(args.name or hint_repo or root.name)
    if args.name:
        name = slugify(args.name)
    else:
        tries = 0
        while True:
            if tries >= MAX_ASK_RETRY:
                die("没能问出合法仓库名（只能用字母数字、- _ .）。也可用 --name 指定")
            tries += 1
            name = slugify(ask("④ 新建的仓库叫什么？", default_name, False)) or default_name
            if name:
                break
            bad("仓库名不能为空——目录名是中文时请手输一个英文名")
    if args.private is not None:
        private = bool(args.private)
    elif args.public:
        private = False
    else:
        private = ask_bool("   建成私有仓库？", bool(cfg["defaults"].get("private", True)), False)
    ok(f"仓库：{name}  ·  {'私有' if private else '公开'}")
    info("确认无误后开始：建库 → 提交 → 推送")

    return dict(platform=platform, login=login_hint, repo=name,
                private=private, dir=str(root), token=token)


# --------------------------------------------------------------------------
# git 基础操作
# --------------------------------------------------------------------------
def ensure_git_available():
    proc = run(["git", "--version"])
    if proc.returncode != 0:
        die("没找到 git，请先安装 Git for Windows")
    version = proc.stdout.strip()
    ok(version)
    m = re.search(r"(\d+)\.(\d+)", version)
    if m and (int(m.group(1)), int(m.group(2))) < (2, 24):
        warn("git 版本偏低，某些行为可能与预期不同")
    return version


def current_branch(root):
    """未提交的仓库里 rev-parse --abbrev-ref HEAD 会返回 'HEAD'，
    这时必须用 symbolic-ref 才能拿到真正的分支名。"""
    name = git_out(["symbolic-ref", "--short", "HEAD"], root)
    if name and name != "HEAD":
        return name
    name = git_out(["rev-parse", "--abbrev-ref", "HEAD"], root)
    return name if name and name != "HEAD" else ""


def ensure_repo(root):
    if (root / ".git").exists():
        branch = current_branch(root)
        if branch:
            ok(f"已经是 git 仓库，当前分支 {branch}")
        else:
            ok("已经是 git 仓库（处于游离 HEAD 状态）")
        return branch
    parent = git_out(["rev-parse", "--show-toplevel"], root)
    if parent and Path(parent) != root:
        warn(f"该目录位于另一个仓库内部（{parent}）")
        if not ask_bool("仍然在此目录单独建仓库？", False):
            die("已取消")
    if git(["init", "-b", "main"], cwd=root).returncode != 0:
        git(["init"], cwd=root, check=True)
        git(["checkout", "-b", "main"], cwd=root)
    ok("已初始化仓库（分支 main）")
    return "main"


def ensure_identity(root, cfg, args):
    name = (args.user_name or cfg["identity"].get("name")
            or git_out(["config", "--get", "user.name"], root))
    email = (args.user_email or cfg["identity"].get("email")
             or git_out(["config", "--get", "user.email"], root))

    if not name:
        name = ask("提交者姓名", os.environ.get("USERNAME") or "developer", args.yes)
    if not email:
        email = ask("提交者邮箱", "", args.yes)
    if not email or "@" not in email:
        die("提交者邮箱缺失或格式不对。用 --user-email 指定，或在配置里填好")

    git(["config", "--local", "user.name", name], cwd=root, check=True)
    git(["config", "--local", "user.email", email], cwd=root, check=True)
    if name != cfg["identity"].get("name") or email != cfg["identity"].get("email"):
        cfg["identity"] = {"name": name, "email": email}
        save_config(cfg)
    ok(f"提交身份：{name} <{email}>（写入本仓库 .git/config）")
    return name, email


def ensure_gitignore(root, args):
    path = root / ".gitignore"
    if args.no_gitignore:
        warn("按参数要求跳过 .gitignore")
        return
    if path.exists():
        ok(".gitignore 已存在，不改动")
        return
    path.write_text(GITIGNORE_TEMPLATE, encoding="utf-8")
    ok("已生成 .gitignore（Python / 虚拟环境 / 模型权重 / 数据集产物 / IDE）")
    info("如果里面有你想上传的目录，删掉对应行后重跑")


def staged_files(root):
    """返回 [(rel, size), ...]，来源是 git 自己的暂存清单，权威。"""
    proc = git(["diff", "--cached", "--name-only", "-z"], cwd=root)
    names = [n for n in (proc.stdout or "").split("\0") if n]
    result = []
    for rel in names:
        try:
            size = (root / rel).stat().st_size
        except OSError:
            size = 0
        result.append((rel.replace("\\", "/"), size))
    return result


def staged_deletions(root):
    """暂存区里「被删除」的文件名。

    删除也是改动，但它体积是 0——只报「将推送 N 个文件，合计 0 B」会让人以为没东西推。
    所以单独列出来（实测 2026-09-22：删掉 helloai.py 时报告就是这样）。
    """
    proc = git(["diff", "--cached", "--name-only", "-z", "--diff-filter=D"], cwd=root)
    return [n.replace("\\", "/") for n in (proc.stdout or "").split("\0") if n]


def stage_all(root):
    """先清空暂存区再重新 add，这样每轮改动过的 .gitignore 都能正确生效
    （.gitignore 对已暂存的文件不生效，必须先 reset）。"""
    git(["reset", "-q"], cwd=root)
    proc = git(["add", "-A"], cwd=root)
    if proc.returncode != 0:
        die(f"git add 失败：{(proc.stderr or proc.stdout).strip()}")


# --------------------------------------------------------------------------
# 体检（对暂存区）
# --------------------------------------------------------------------------
def scan_staged(root, files):
    report = {"count": len(files), "total_bytes": sum(s for _, s in files),
              "big": [], "secrets_name": [], "secrets_content": [],
              "ignored_dirs": [], "crlf_files": [], "scanned": 0,
              "deleted": staged_deletions(root)}
    name_res = [(re.compile(p, re.IGNORECASE), label)
                for p, label in SECRET_FILENAME_PATTERNS]
    content_res = [(re.compile(p), label) for p, label in SECRET_CONTENT_PATTERNS]

    for rel, size in files:
        base = os.path.basename(rel)
        if size >= SINGLE_WARN:
            report["big"].append({"rel": rel, "size": size})
        for rx, label in name_res:
            if rx.search(base):
                report["secrets_name"].append({"rel": rel, "kind": label})
                break
        if rel.lower().endswith((".bat", ".cmd")):
            report["crlf_files"].append(rel)

        if size > CONTENT_SCAN_MAX_BYTES or report["scanned"] >= CONTENT_SCAN_MAX_FILES:
            continue
        report["scanned"] += 1
        try:
            blob = (root / rel).read_bytes()
        except OSError:
            continue
        if b"\x00" in blob[:1024]:
            continue
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        hit = None
        for lineno, line in enumerate(text.splitlines(), 1):
            if len(line) > 4000:
                continue
            for rx, label in content_res:
                if rx.search(line):
                    hit = {"rel": rel, "line": lineno, "kind": label}
                    break
            if hit:
                break
        if hit:
            report["secrets_content"].append(hit)

    report["big"].sort(key=lambda d: -d["size"])
    for probe in IGNORED_DIR_PROBES:
        if (root / probe).is_dir():
            report["ignored_dirs"].append(probe + "/")
    return report


def print_scan(report, platforms):
    head("一、推送前体检")

    if report["count"] == 0:
        warn("暂存区是空的，没有任何文件会被推送")
        if report["ignored_dirs"]:
            info(f"可能是 .gitignore 排除了：{', '.join(report['ignored_dirs'])}")
        return

    info(f"将推送 {report['count']} 项改动，合计 {human(report['total_bytes'])}")
    if report["deleted"]:
        names = "、".join(report["deleted"][:5])
        tail = f" 等 {len(report['deleted'])} 个" if len(report["deleted"]) > 5 else ""
        info(f"其中 {len(report['deleted'])} 项是删除（{names}{tail}）"
             f"——远端对应文件会一并消失，这是故意的")
    info(f"已逐个查密钥的文件：≤{CONTENT_SCAN_MAX_FILES} 个 · "
         f"单文件上限 {human(CONTENT_SCAN_MAX_BYTES)}")
    if report["ignored_dirs"]:
        info(f"已被 .gitignore 排除（体积不计入）：{', '.join(report['ignored_dirs'])}")

    for platform in platforms:
        limit = SINGLE_BLOCK[platform]
        over = [b for b in report["big"] if b["size"] >= limit]
        near = [b for b in report["big"] if b["size"] < limit]
        if not over and not near:
            continue
        out()
        out(f"  {platform} 单文件上限 {human(limit)}：")
        for item in over[:8]:
            out(f"    - [超限] {human(item['size']):>10}  {item['rel']}")
        for item in near[:8]:
            out(f"    - [偏大] {human(item['size']):>10}  {item['rel']}")
        if len(over) + len(near) > 16:
            info("…更多已省略")

    if report["secrets_name"]:
        out()
        out(f"  疑似密钥文件（{len(report['secrets_name'])} 个）：")
        for item in report["secrets_name"][:12]:
            out(f"    - [{item['kind']}] {item['rel']}")

    if report["secrets_content"]:
        out()
        out(f"  文件内容疑似硬编码密钥（{len(report['secrets_content'])} 处）：")
        for item in report["secrets_content"][:12]:
            out(f"    - {item['rel']}:{item['line']}  [{item['kind']}]")

    if report["crlf_files"]:
        out()
        warn(f"含 {len(report['crlf_files'])} 个 .bat/.cmd 文件")
        info("本机 core.autocrlf=true，仓库里会存成 LF。需要原样保留 CRLF 的话，")
        info("加一个 .gitattributes 写：*.bat -text   *.cmd -text")

    out()
    if report["secrets_name"] or report["secrets_content"]:
        warn("命中疑似密钥。默认中止，确认无误后加 --allow-secrets 继续。")
    else:
        ok("未在暂存内容里发现明显的密钥泄漏风险")


def size_gate(report, platforms, args):
    over = []
    for platform in platforms:
        limit = SINGLE_BLOCK[platform]
        for item in report["big"]:
            if item["size"] >= limit:
                over.append((platform, item, limit))
    if not over:
        if report["total_bytes"] >= REPO_WARN:
            warn(f"整仓库 {human(report['total_bytes'])}，已接近 Gitee 单仓库 500 MB 配额")
        return True
    out()
    bad("有文件超过平台单文件上限，push 一定会失败")
    seen = set()
    for platform, item, limit in over:
        if item["rel"] in seen:
            continue
        seen.add(item["rel"])
        info(f"{human(item['size']):>10}  {item['rel']}（{platform} 上限 {human(limit)}）")
    info("处理办法：把该目录加进 .gitignore，或改用 Release 附件分发大文件")
    if args.allow_large:
        warn("按 --allow-large 继续，但远端大概率拒收")
        return True
    return False


# --------------------------------------------------------------------------
# 远端
# --------------------------------------------------------------------------
def remote_matches(url, platform, repo, login=""):
    """这个远端地址是不是「就是本平台、本账号下的目标仓库」——按地址判断，不看远端名。

    login 给得出就一并比对属主，避免把别人账号下的同名仓库当成自己的复用。
    """
    if HOSTS[platform] not in url:
        return False
    tail = url.split("@")[-1].rstrip("/")          # 去掉 user@host 的账号前缀
    if tail.endswith(".git"):
        tail = tail[:-4]
    seg = [s for s in re.split(r"[/:]", tail) if s]
    if not seg or seg[-1].lower() != repo.lower():
        return False
    return not login or len(seg) < 2 or seg[-2].lower() == login.lower()


def resolve_remotes(root, platforms, proto, cfg, args):
    existing = {}
    for name in (git(["remote"], cwd=root).stdout or "").split():
        existing[name] = git_out(["remote", "get-url", name], root)

    plan, used = [], set(existing)
    repo = cfg["_repo"]
    for index, platform in enumerate(platforms):
        # 先按「地址」找可复用的远端，名字是次要的。
        # 踩过（2026-09-22）：origin 给了 gitee 之后再单跑 --platform github，
        # 旧逻辑只认名字 origin/mirror，没去复用已经指向 github 的 mirror，
        # 结果又新加了个 github-remote——同一个仓库挂两个远端名。
        login = cfg["logins"].get(platform, "")
        matched = next((n for n in existing
                        if remote_matches(existing[n], platform, repo, login)), None)
        if matched:
            ok(f"沿用已有远端 {matched} → {existing[matched]}")
            plan.append((platform, matched))
            continue

        name = "origin" if index == 0 else "mirror"
        if name in existing:
            for alt in ("mirror" if name == "origin" else "origin",
                        f"{platform}-remote"):
                if alt not in used:
                    name = alt
                    break
            else:
                name = f"{platform}-remote2"
        url = remote_url(platform, login, cfg["_repo"], proto)
        git(["remote", "add", name, url], cwd=root, check=True)
        ok(f"远端 {name} → {url}")
        used.add(name)
        plan.append((platform, name))
    return plan


def read_remote_sha(root, prefix, target, branch, env):
    """读远端 refs/heads/<branch> 的 SHA。这是判断推送是否真的成功的唯一可靠依据。"""
    proc = run(prefix + ["ls-remote", target, f"refs/heads/{branch}"],
               cwd=root, extra_env=env, timeout=60)
    if proc.returncode != 0:
        return "", (proc.stderr or proc.stdout).strip()
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if parts:
            return parts[0].strip(), ""
    return "", ""


def ensure_remote_ref(root, remote, branch, target, prefix, env):
    """让 refs/remotes/<remote>/<branch> 真实存在。返回 'fetch' / 'manual' / ''。

    正常路径是 `git fetch`。但本机实测：git 写 refs/remotes/** 和 refs/tags/**
    会**静默失败**——退出码 0、没有任何报错，文件却不存在（连 `git update-ref`
    也一样），而写 refs/heads/** 正常。所以 fetch 之后必须再验一次引用在不在，
    不在就直接写 ref 文件（本机 ref 后端是传统 loose-ref，已验证有效）。
    正常机器上第一次校验就通过，不会走兜底分支。
    """
    ref = f"refs/remotes/{remote}/{branch}"
    head = git_out(["rev-parse", "HEAD"], root)
    run(prefix + ["fetch", "--no-tags", target, f"+{branch}:{ref}"],
        cwd=root, extra_env=env, timeout=PUSH_ATTEMPT_TIMEOUT)
    # 必须比对**内容**而不是"存在性"：本机 git 更新 refs/remotes 也会静默失败，
    # 引用可能早就在（上次兜底写的），但内容是过期的旧值——只看存在会误判成功。
    if head and git_out(["rev-parse", "--verify", "--quiet", ref], root) == head:
        return "fetch"

    if not head:
        return ""
    path = Path(root) / ".git" / "refs" / "remotes" / remote / branch
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(head + "\n", encoding="ascii")
    except OSError:
        return ""
    return "manual"


def push_platform(root, platform, remote, branch, login, repo, token, proto, args):
    """分级重试推送，并且以远端 refs 校验真实结果（不信 push 的退出码）。

    第 1 级 直连  → 第 2 级 清代理 → （仅 GitHub）第 3 级 清代理 + 绑 IP
    只有判定为网络类错误才升级到下一级，权限类错误立即停下。
    """
    host = HOSTS[platform]
    target = remote if proto == "ssh" else auth_url(platform, login, repo, token)
    local_sha = git_out(["rev-parse", "HEAD"], root)

    # 阶梯顺序不是随手排的：
    #   ① 直连（保留用户代理）——正常情况一次就过，Gitee 基本都走这条；
    #   ② 直连 + HTTP/1.1——链路干扰把 HTTP/2 掐断时救命（见 git_prefix 的说明）；
    #   ③④ 清代理（+HTTP/1.1）——代理本身坏了或指向错节点时；
    #   ⑤ GitHub 专有：清代理 + 绑 DNS 当前 IP + HTTP/1.1——DNS 被污染时。
    # 每级都用 git ls-remote 验结果，权限类错误在第一级就停下，不会白跑后面几级。
    attempts = [
        ("直连", None, False, False),
        ("直连 + HTTP/1.1", None, False, True),
        ("清代理", None, True, False),
        ("清代理 + HTTP/1.1", None, True, True),
    ]
    if platform == "github":
        # 「绑 IP」这一级必须用**当前 DNS 解析出来的**地址，而不是写死的老 IP。
        # 教训（2026-09-23）：写死的 140.82.11x.4 是美国段，本机实际解析到
        # 20.205.243.166（亚太段）——绑到老段上等于往一个更远的、多半不通的地址打，
        # 这一级不但没兜底，还白等一次超时。现在先信 DNS，DNS 拿不到才用备用段。
        ips = github_ips()[:1]
        for ip in ips:
            attempts.append((f"清代理 + 绑 IP {ip} + HTTP/1.1", ip, True, True))

    deadline = time.time() + PUSH_TOTAL_BUDGET
    last_err = ""
    for label, ip, strip_proxy, http1 in attempts:
        if time.time() > deadline:
            last_err = (last_err or "网络不通") + f"\n（重试总时长已超过 {PUSH_TOTAL_BUDGET} 秒，停止升级）"
            break
        prefix = git_prefix(host, ip, http1)
        env = net_env(strip_proxy)
        push_spec = (["push", "-u", remote, branch] if proto == "ssh"
                     else ["push", target, f"{branch}:{branch}"])
        proc = run(prefix + push_spec, cwd=root, extra_env=env, timeout=PUSH_ATTEMPT_TIMEOUT)
        output = clean_output((proc.stderr or "") + (proc.stdout or ""))

        # 不看退出码，直接问远端
        remote_sha, _ = read_remote_sha(root, prefix, target, branch, env)
        if remote_sha and remote_sha == local_sha:
            mode = ""
            if proto != "ssh":
                git(["config", f"branch.{branch}.remote", remote], cwd=root)
                git(["config", f"branch.{branch}.merge",
                     f"refs/heads/{branch}"], cwd=root)
                # 补上 remote-tracking ref。推送走的是「带令牌的显式 URL」而不是 remote 名，
                # 所以 git 不会自己建 refs/remotes/<remote>/<branch>。不补的话：
                # ① git status / PyCharm 看不到与远端的对比；② 用户随后裸跑
                # `git fetch`/`git ls-remote` 会因为拿不到凭据而报
                # "could not read Username for 'https://gitee.com'"（真踩过）。
                mode = ensure_remote_ref(root, remote, branch, target, prefix, env)
            if args.remember_credentials and proto == "https" and token:
                payload = (f"protocol=https\nhost={host}\n"
                           f"username={login}\npassword={token}\n\n")
                try:
                    run(["git", "credential", "approve"], cwd=root,
                        stdin_text=payload, extra_env=net_env(), timeout=20)
                except Exception:
                    pass
            note = ("" if proc.returncode == 0
                    else "远端 refs 已一致，但 push 没打印成功输出（静默成功）")
            if mode == "manual":
                extra = (f"本机 git 写 refs/remotes/{remote}/{branch} 会静默失败，"
                         f"已用兜底方式补上该跟踪引用")
                note = f"{note}；{extra}" if note else extra
            return True, "", label, note

        last_err = output or "未收到远端响应"
        if not is_network_error(last_err):
            break
    return False, last_err, "", ""


# --------------------------------------------------------------------------
# 配置展示
# --------------------------------------------------------------------------
def print_config(cfg):
    head("当前配置")
    info(f"配置文件：{CONFIG_PATH}")
    ident = cfg["identity"]
    info(f"提交者：{ident.get('name') or '(未设置)'} "
         f"<{ident.get('email') or '(未设置)'}>")
    for platform in PLATFORMS:
        info(f"{platform:<7} 令牌 {mask(cfg['tokens'].get(platform)):<18} "
             f"账号 {cfg['logins'].get(platform) or '(未探测)'}")
    d = cfg["defaults"]
    info(f"默认平台：{', '.join(d.get('platforms', ['gitee']))}   "
         f"私有：{'是' if d.get('private', True) else '否'}   "
         f"协议：{d.get('proto', 'https')}")
    out()
    info("设置令牌：python git_sync.py --set-token gitee=你的私人令牌")
    info("设置身份：python git_sync.py --user-name 张三 --user-email a@b.com")


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(
        prog="git_sync.py",
        description="把本地项目一键同步到 Gitee / GitHub",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "直接运行（不带参数）会进入四问向导：\n"
            "  ① 传到哪个账号？  ② 该账号的私人令牌\n"
            "  ③ 要上传哪个文件夹？  ④ 新建的仓库叫什么？\n"
            "  问完自动建库 → 提交 → 推送，不必先上网建空仓库。\n"
            "\n"
            "不想被问就加 --yes 并用参数给全，例如：\n"
            "  git_sync.py --dir ./myproj --account https://gitee.com/me/ --yes\n"
            "两个平台账号名不一样时，各给一个 --account：\n"
            "  git_sync.py --dir ./myproj --platform both \\\n"
            "      --account https://gitee.com/me/ --account https://github.com/me-gh/ --yes\n"))
    ap.add_argument("--dir", default=None, help="项目目录，默认问你要（回车用当前目录）")
    ap.add_argument("--account", action="append",
                    help="目标账号，如 https://gitee.com/你的用户名/。"
                         "可给多次（每个平台一个），例如两个平台账号名不同时："
                         "--account https://gitee.com/A/ --account https://github.com/B/")
    ap.add_argument("--name", help="远端仓库名，默认取目录名")
    ap.add_argument("--desc", default="", help="仓库描述")
    ap.add_argument("--platform", help="gitee / github / both，默认读配置")
    ap.add_argument("--private", action="store_true", default=None, help="建私有仓库")
    ap.add_argument("--public", action="store_true", help="建公开仓库")
    ap.add_argument("--branch", help="分支名，默认沿用仓库当前分支")
    ap.add_argument("--message", help="提交说明")
    ap.add_argument("--proto", choices=["https", "ssh"], help="推送协议，默认 https")
    ap.add_argument("--yes", action="store_true", help="全自动，绝不询问")
    ap.add_argument("--dry-run", action="store_true", help="只体检出计划，不提交不推送")
    ap.add_argument("--no-gitignore", action="store_true", help="不生成 .gitignore")
    ap.add_argument("--allow-secrets", action="store_true", help="命中疑似密钥也继续")
    ap.add_argument("--allow-large", action="store_true", help="超限文件也继续尝试")
    ap.add_argument("--remember-credentials", action="store_true",
                    help="推成功后把令牌交给系统凭据管理器（可能弹窗，谨慎）")
    ap.add_argument("--user-name", help="git 提交者姓名（同时存入配置）")
    ap.add_argument("--user-email", help="git 提交者邮箱（同时存入配置）")
    ap.add_argument("--set-token", metavar="PLATFORM=TOKEN",
                    help="保存平台令牌，例如 gitee=abc123")
    ap.add_argument("--show-config", action="store_true", help="打印当前配置")
    ap.add_argument("--intro", action="store_true",
                    help="打印「我能做什么 / 需要你提供什么」的介绍后退出")
    ap.add_argument("--version", action="version", version=f"git-sync {VERSION}")
    return ap


def main():
    _setup_console()
    args = build_parser().parse_args()
    cfg = load_config()

    if args.set_token:
        if "=" not in args.set_token:
            die("--set-token 格式应为 PLATFORM=TOKEN，例如 gitee=abc123")
        platform, _, token = args.set_token.partition("=")
        platform, token = platform.strip().lower(), token.strip()
        if platform not in PLATFORMS:
            die(f"平台只能是 {' 或 '.join(PLATFORMS)}")
        if not token:
            die("令牌是空的")
        cfg["tokens"][platform] = token
        save_config(cfg)
        login, err = api_login(platform, token)
        if login:
            cfg["logins"][platform] = login
            save_config(cfg)
            ok(f"{platform} 令牌已保存，账号识别为 {login}")
        else:
            warn(f"令牌已保存，但校验没通过：{err}")
            info(TOKEN_SCOPE_HINT[platform])
        return

    if args.user_name or args.user_email:
        cfg["identity"]["name"] = args.user_name or cfg["identity"].get("name", "")
        cfg["identity"]["email"] = args.user_email or cfg["identity"].get("email", "")
        save_config(cfg)
        ok(f"提交身份已保存：{cfg['identity']['name']} <{cfg['identity']['email']}>")
        # 纯设置操作到此为止。否则 --dir 为空会退化成"当前目录"，
        # 把一个跟本次操作无关的目录初始化成 git 仓库（真踩过：配身份顺手污染了工作区根目录）。
        if not (args.account or args.dir is not None or args.yes
                or args.show_config or args.intro):
            info("只保存了身份，没有开始同步。")
            info("要开始同步：加 --dir <项目目录>，或直接运行本脚本进入向导。")
            return

    if args.show_config:
        print_config(cfg)
        return

    if args.intro:
        print_intro()
        return

    # ---- 账号地址（--account 或向导第①问）----
    # 按平台存期望值。两个平台的账号名常常不一样（实测用户就是 zhangsan / lisi），
    # 所以校验必须按平台分开做——否则 --platform both 配单个 --account 时，
    # 总有一个平台被判成"账号对不上"而被跳过，"一次推两个平台"根本跑不起来。
    expect_logins, hint_repo = {}, None
    if args.account:
        for raw in args.account:
            p, lg, rp, err = parse_account(raw)
            if not lg:
                die(f"--account 解析失败：{err}")
            if not p:
                die("账号地址要带平台，例如 https://gitee.com/用户名/")
            expect_logins[p] = lg
            hint_repo = hint_repo or rp
        # 没显式给 --platform 时：给了一个地址就用那个平台，给了多个就都上
        if not args.platform:
            args.platform = ",".join(expect_logins)

    # ---- 交互模式就是向导模式：四问之后全自动 ----
    wizard = None
    if INTERACTIVE and not args.yes:
        wizard = wizard_collect(args, cfg, expect_logins=expect_logins,
                                hint_repo=hint_repo)
        args.dir = wizard["dir"]
        args.name = wizard["repo"]
        args.platform = wizard["platform"]
        args.private = bool(wizard["private"])
        if wizard["login"]:
            expect_logins.setdefault(wizard["platform"], wizard["login"])

    start = time.time()
    root = Path(args.dir or ".").expanduser().resolve()
    if not root.is_dir():
        die(f"目录不存在：{root}")

    head("git-sync —— 本地项目同步到 Gitee / GitHub")
    info(f"项目目录：{root}")
    if args.dir is None:
        # 没给 --dir 就只能落到当前目录，这里明说一句，别让它静默发生
        # （曾经因为一条设置身份的命令顺带跑进主流程，把无关目录初始化成了仓库）。
        warn("你没指定 --dir，按当前目录处理。要同步别处请加 --dir <项目目录>")
    info(f"运行模式：{'全自动' if args.yes else '向导已完成'}"
         f"{'  · 演练' if args.dry_run else ''}")

    # ---- 平台 / 仓库名 / 可见性 / 协议 ----
    if args.platform:
        platforms = [p for p in re.split(r"[,\s]+", args.platform.strip()) if p]
    else:
        platforms = list(cfg["defaults"].get("platforms") or ["gitee"])
    platforms = [p.lower() for p in platforms]
    if "both" in platforms:
        platforms = list(PLATFORMS)
    unknown = [p for p in platforms if p not in PLATFORMS]
    if unknown or not platforms:
        die(f"平台不合法：{unknown or platforms}，只能是 gitee / github / both")
    # 给了账号地址的按平台核对归属；没给的平台只能信令牌自己报的账号。
    # 说一句，别让"没核对"这件事悄悄过去。
    for p in platforms:
        if expect_logins and p not in expect_logins:
            warn(f"{p}：没给账号地址，跳过账号归属核对"
                 f"（会用令牌实际所属的账号，可用 --account 指明）")

    repo_name = slugify(args.name or root.name)
    if INTERACTIVE and not args.yes and wizard is None:
        repo_name = slugify(ask("远端仓库名", repo_name, args.yes)) or repo_name
    if not repo_name:
        die("仓库名解析为空（目录名是中文？），请用 --name 指定英文仓库名")

    if args.private is not None:
        private = bool(args.private)
    elif args.public:
        private = False
    else:
        private = bool(cfg["defaults"].get("private", True))
    if INTERACTIVE and not args.yes and wizard is None:
        private = ask_bool("建私有仓库？", private, args.yes)

    proto = args.proto or cfg["defaults"].get("proto", "https")

    # ---- 令牌 ----
    tokens = {}
    for platform in platforms:
        token = (cfg["tokens"].get(platform)
                 or os.environ.get(f"{platform.upper()}_TOKEN", ""))
        if not token and proto == "https" and INTERACTIVE and not args.yes and wizard is None:
            token = ask(f"{platform} 私人令牌（留空则跳过该平台）", "", args.yes)
            if token:
                cfg["tokens"][platform] = token
                save_config(cfg)
        tokens[platform] = token
        if not token:
            warn(f"没有 {platform} 令牌 → 将跳过 {platform} 的建库与推送")

    # ---- 本地仓库 + 暂存 ----
    head("一、准备本地仓库")
    ensure_git_available()
    current = ensure_repo(root)
    if current:
        if args.branch and args.branch != current:
            warn(f"当前在 {current} 分支，但指定了 {args.branch}；将推送 {args.branch}")
            branch = args.branch
        else:
            branch = current
    else:
        branch = args.branch or cfg["defaults"].get("branch", "main")
    first_commit = git(["rev-parse", "--verify", "HEAD"], cwd=root).returncode != 0
    ensure_identity(root, cfg, args)
    ensure_gitignore(root, args)
    stage_all(root)
    files = staged_files(root)
    ok(f"已暂存 {len(files)} 个文件")

    # ---- 体检（针对暂存区）----
    report = scan_staged(root, files)
    print_scan(report, platforms)

    if (report["secrets_name"] or report["secrets_content"]) and not args.allow_secrets:
        out()
        bad("检出疑似密钥，已中止：未提交、未推送")
        info("本地已初始化仓库并暂存了文件，不满意可以删掉 .git 目录重来")
        info("处理办法：把敏感文件加进 .gitignore 后重跑，或确认无误加 --allow-secrets")
        sys.exit(2)

    if report["count"] == 0 and first_commit:
        out()
        bad("暂存区是空的，这个仓库没有任何文件可提交")
        info("检查 .gitignore 是否把文件都排除了，或用 --no-gitignore 重跑")
        sys.exit(3)

    if report["count"] and not size_gate(report, platforms, args):
        sys.exit(4)

    if args.dry_run:
        head("演练结束")
        info("以上仅为体检结果，未提交、未推送")
        info(f"计划：分支 {branch} · 平台 {', '.join(platforms)} · 仓库 {repo_name} · "
             f"{'私有' if private else '公开'} · {proto}")
        return

    # ---- 提交 ----
    head("二、提交")
    if report["count"] == 0:
        info("工作区没有新改动，跳过提交（直接检查远端）")
    else:
        default_msg = (f"chore: 首次提交（{datetime.now():%Y-%m-%d}）" if first_commit
                       else f"sync: {datetime.now():%Y-%m-%d %H:%M}")
        message = args.message or ask("提交说明", default_msg, args.yes)
        proc = git(["commit", "-m", message], cwd=root)
        if proc.returncode != 0:
            die(f"提交失败：\n         {(proc.stderr or proc.stdout).strip()}")
        ok(f"已提交 {report['count']} 个文件：{message}")

    # ---- 远端 ----
    head("三、准备远端仓库")
    live, logins = [], {}
    for platform in platforms:
        token = tokens.get(platform, "")
        if not token:
            continue
        login, err = api_login(platform, token)
        if not login:
            bad(f"{platform} 令牌校验失败：{err}")
            continue
        expect_login = expect_logins.get(platform)
        if login_mismatch(expect_login, login):
            bad(f"{platform} 账号对不上：你给的地址是 {expect_login}，"
                f"但这枚令牌属于 {login}")
            info("已停下，避免把仓库建到没打算用的账号下。")
            info("要么换成该账号自己的令牌，要么把 --account 改成正确地址。")
            continue
        logins[platform] = login
        cfg["logins"][platform] = login
        save_config(cfg)
        created, is_new, err = api_create_repo(platform, token, repo_name,
                                               private, args.desc)
        if not created:
            bad(f"{platform} 建库失败：{err}")
            for line in create_error_hint(err):
                info(line)
            continue
        ok(f"{platform} → {web_url(platform, login, repo_name)}"
           f"{'（新建）' if is_new else '（已存在，复用）'}")
        live.append(platform)

    if not live:
        head("结束")
        warn("没有可用平台，只在本地完成了提交")
        info("设置令牌：python git_sync.py --set-token gitee=你的私人令牌")
        return

    # ---- 推送 ----
    head("四、推送到远端")
    cfg["_repo"] = repo_name
    plan = resolve_remotes(root, live, proto, cfg, args)
    results = []
    for platform, remote in plan:
        login = cfg["logins"][platform]
        pushed, err, via, note = push_platform(root, platform, remote, branch, login,
                                              repo_name, tokens[platform], proto, args)
        if pushed:
            ok(f"{platform} 推送成功（{via}）→ {web_url(platform, login, repo_name)}")
            info("已用 git ls-remote 核对远端 refs 与本地 HEAD 一致")
            if note:
                warn(note)
        else:
            bad(f"{platform} 推送失败")
            info(err.replace("\n", "\n         ")[:800])
            low = err.lower()
            if "remote rejected" in low and "workflow" in low:
                info("提示：这是 workflow scope 问题（不是 403）。令牌需要单独勾")
                info("      Workflows 权限；否则先把 .github/workflows/ 下的文件挪出去")
            elif "large file" in low or "exceeds" in low or "too large" in low:
                info("提示：文件超限。大文件请走 Release 附件，不要塞进仓库")
            elif "not found" in low or "404" in low:
                info("提示：GitHub 对私有仓库也会回 Repository not found（刻意隐藏）；")
                info("      确认令牌勾了 repo 写权限、账号与仓库属主一致")
            elif "403" in low or "401" in low or "authentication" in low:
                info("提示：令牌无写权限。可用带令牌的 URL 直推一次来区分是")
                info("      「令牌本身没权限」还是「凭据串号」——两者都报 403")
            elif is_network_error(err):
                info("提示：网络链路问题。已自动试过清代理 / 绑 IP 仍失败，")
                info("      检查是否必须走代理才能访问该平台")
        results.append((platform, pushed))

    # ---- 汇总 ----
    head("完成")
    info(f"仓库 {repo_name} · 分支 {branch} · "
         f"{'私有' if private else '公开'} · 用时 {time.time() - start:.1f} 秒")
    for platform, pushed in results:
        info(f"{platform:<7} {'已同步' if pushed else '失败'}  "
             f"{web_url(platform, cfg['logins'][platform], repo_name)}")
    out()
    info("以后同步：在本目录运行  python git_sync.py --yes")
    if not args.remember_credentials and proto == "https":
        # 措辞避免写死具体助手名：2026-09-22 本机已从 helper-selector 换成 wincred，
        # 但别的机器可能仍然挂着会弹 GUI 的那种。
        info("若在 PyCharm / 别处点 push 被弹窗卡住，说明本机凭据助手需要 GUI 交互")
        info("（例：PortableGit 自带的 helper-selector）。推送改用本脚本，")
        info("或把令牌交给凭据管理器：--remember-credentials")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        out()
        die("已中断")
