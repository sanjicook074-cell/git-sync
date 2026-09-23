# -*- coding: utf-8 -*-
"""git-sync v1.2 新增健壮性逻辑的测试（本地裸仓库代替远端，不联网）"""
import atexit
import os
import socket
import subprocess
import sys
import shutil
import stat
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL))
import git_sync as G  # noqa: E402


def rmtree_force(path):
    """Windows 上 git 会把 `.git/objects/**` 写成**只读**，`shutil.rmtree` 直接
    `PermissionError [WinError 5]`；而 `ignore_errors=True` 会把这个异常静默吞掉，
    于是临时目录永远清不掉——真踩过：每跑一次测试就永久留一个目录。
    正确做法是先去掉只读位再重试。
    """
    def onerror(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    shutil.rmtree(path, onerror=onerror)


def purge_stale(prefix, max_age=1800):
    """清掉上次被强杀（timeout / 中断，atexit 跑不到）留下的同前缀目录。

    只删超过 max_age 秒没被动过的——避免误删并发运行的另一个测试的目录。
    """
    now = time.time()
    for d in Path(tempfile.gettempdir()).glob(prefix + "*"):
        try:
            if d.is_dir() and now - d.stat().st_mtime > max_age:
                rmtree_force(d)
        except OSError:
            pass


ROB_PREFIX = "gs_test_"
purge_stale(ROB_PREFIX)
BASE = Path(tempfile.mkdtemp(prefix=ROB_PREFIX))
atexit.register(rmtree_force, BASE)  # 中途异常 / sys.exit 也要清
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'[OK]  ' if cond else '[FAIL]'} {name}" + (f"  {detail}" if detail else ""))


def sh(cmd, cwd=None, env=None):
    e = os.environ.copy()
    e.update(env or {})
    return subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


print("=" * 60)
print("1) net_env / is_network_error / git_prefix")
print("=" * 60)
e1 = G.net_env(False)
e2 = G.net_env(True)
check("GIT_CONFIG_SYSTEM 指向空设备", e1.get("GIT_CONFIG_SYSTEM") == G.NULL_DEVICE,
      f"-> {e1.get('GIT_CONFIG_SYSTEM')!r}")
check("默认不动代理变量", "http_proxy" not in e1)
check("strip 模式标记删除代理变量", e2.get("http_proxy") is None
      and e2.get("ALL_PROXY") is None)
check("GIT_TERMINAL_PROMPT=0", e1.get("GIT_TERMINAL_PROMPT") == "0")

check("识别 schannel 代理症状",
      G.is_network_error("fatal: unable to access: schannel: failed to receive handshake"))
check("识别 server closed abruptly",
      G.is_network_error("schannel: server closed abruptly (missing close_notify)"))
check("不误判权限错误", not G.is_network_error(
    "remote: Permission to a/b.git denied to c. fatal: ... 403"))
check("不误判 workflow rejected",
      not G.is_network_error(
          "! [remote rejected] main -> main (refusing to allow a Personal Access Token "
          "to create or update workflow)"))

check("git_prefix 基础", G.git_prefix("gitee.com") == ["git", "-c", "credential.helper="])
check("git_prefix 绑 IP", G.git_prefix("github.com", "1.2.3.4")[-2:]
      == ["-c", "http.curloptResolve=github.com:443:1.2.3.4"])

print()
print("=" * 60)
print("2) 带 GIT_CONFIG_SYSTEM=NUL 时 git 能否正常工作")
print("=" * 60)
repo = BASE / "proj"
repo.mkdir()
(repo / "a.txt").write_text("hello\n", encoding="utf-8")
bare = BASE / "bare.git"
sh(["git", "init", "--bare", "-q", str(bare)])
for cmd in (["git", "init", "-b", "main", "-q"],
            ["git", "config", "user.name", "T"],
            ["git", "config", "user.email", "t@e.com"],
            ["git", "add", "-A"],
            ["git", "commit", "-q", "-m", "first"]):
    r = sh(cmd, cwd=str(repo), env=G.net_env())
    if r.returncode != 0:
        print("   ", cmd, r.stderr.strip())
check("NUL 环境下 commit 正常",
      sh(["git", "rev-parse", "HEAD"], cwd=str(repo), env=G.net_env()).returncode == 0)

print()
print("=" * 60)
print("3) read_remote_sha —— 以远端 refs 为准（本地裸仓库当远端）")
print("=" * 60)
sha, err = G.read_remote_sha(repo, G.git_prefix("gitee.com"), str(bare), "main", G.net_env())
check("空仓库读不到分支 -> 空字符串（不报错）", sha == "", f"sha={sha!r} err={err[:60]!r}")

local_sha = sh(["git", "rev-parse", "HEAD"], cwd=str(repo)).stdout.strip()
r = sh(["git", "push", str(bare), "main:main"], cwd=str(repo), env=G.net_env())
check("推送成功", r.returncode == 0, r.stderr.strip()[:80])
sha, err = G.read_remote_sha(repo, G.git_prefix("gitee.com"), str(bare), "main", G.net_env())
check("读到的远端 SHA == 本地 HEAD（这就是校验依据）", sha == local_sha,
      f"remote={sha[:8]} local={local_sha[:8]}")

print()
print("=" * 60)
print("4) push_platform 全流程（ssh 模式指向本地裸仓库）")
print("=" * 60)
bare2 = BASE / "bare2.git"
sh(["git", "init", "--bare", "-q", str(bare2)])
args = SimpleNamespace(remember_credentials=False)
repo2 = BASE / "proj2"
repo2.mkdir()
(repo2 / "b.txt").write_text("world\n", encoding="utf-8")
for cmd in (["git", "init", "-b", "main", "-q"],
            ["git", "config", "user.name", "T"],
            ["git", "config", "user.email", "t@e.com"],
            ["git", "add", "-A"],
            ["git", "commit", "-q", "-m", "first"]):
    sh(cmd, cwd=str(repo2), env=G.net_env())

pushed, err, via, note = G.push_platform(repo2, "gitee", str(bare2), "main", "u",
                                         "r", "", "ssh", args)
check("push_platform 返回成功", pushed, f"err={err[:120]!r}")
check("报告了使用的通道（直连）", via == "直连", f"via={via!r}")
check("未误报静默成功", note == "", f"note={note!r}")
local2 = sh(["git", "rev-parse", "HEAD"], cwd=str(repo2)).stdout.strip()
remote2, _ = G.read_remote_sha(repo2, G.git_prefix("gitee.com"), str(bare2), "main", G.net_env())
check("远端确实收到了提交", remote2 == local2, f"{remote2[:8]} vs {local2[:8]}")
up = sh(["git", "config", "--get", "branch.main.remote"], cwd=str(repo2)).stdout.strip()
check("ssh 模式用 -u 设好了 upstream", up == str(bare2) or up != "", f"remote={up}")

print()
print("=" * 60)
print("5) 权限类错误不升级重试（应为单次尝试）")
print("=" * 60)
import time as _t
t0 = _t.time()
pushed, err, via, note = G.push_platform(repo2, "gitee",
                                         "https://gitee.com/definitely-no-such-user-xyz/nope.git",
                                         "main", "u", "r", "fake-token", "https", args)
dt = _t.time() - t0
check("推送失败被正确判定", not pushed)
check("未检测到网络错误时立即停止、不盲目重试", dt < 60, f"耗时 {dt:.1f}s")
check("错误信息非空", bool(err.strip()), f"{err.strip()[:60]!r}")

print()
print("=" * 60)
print("6) ensure_remote_ref —— 本机 git 写 refs/remotes 会静默失败，必须兜底")
print("=" * 60)
repo3 = BASE / "refrepo"
repo3.mkdir(parents=True, exist_ok=True)
sh(["git", "init", "-q", "-b", "main"], cwd=repo3)
(repo3 / "a.txt").write_text("x\n", encoding="utf-8")
sh(["git", "add", "."], cwd=repo3)
sh(["git", "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "init"],
   cwd=repo3)
bare3 = BASE / "refbare.git"
subprocess.run(["git", "init", "-q", "--bare", str(bare3)], check=False)
sh(["git", "-c", "credential.helper=", "push", "-q", str(bare3), "main:main"], cwd=repo3)

# 先记录本机的病态：git update-ref 报告成功，引用却不在
sh(["git", "update-ref", "refs/remotes/probe/main", "HEAD"], cwd=repo3)
probe_exists = (repo3 / ".git" / "refs" / "remotes" / "probe" / "main").exists()

mode = G.ensure_remote_ref(repo3, "origin", "main", str(bare3),
                           G.git_prefix("gitee.com"), G.net_env())
ref_file = repo3 / ".git" / "refs" / "remotes" / "origin" / "main"
head3 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo3, capture_output=True,
                       text=True).stdout.strip()
check("有明确结局（fetch 或 manual）", mode in ("fetch", "manual"), f"mode={mode!r}")
check("目标引用最终真实存在", ref_file.exists(), str(ref_file))
check("写入的 SHA 与 HEAD 一致",
      ref_file.exists() and ref_file.read_text().strip() == head3)
check("git show-ref 能看到它",
      "refs/remotes/origin/main" in subprocess.run(
          ["git", "show-ref"], cwd=repo3, capture_output=True, text=True).stdout)
if not probe_exists:
    print("   （本机确认：update-ref 写 refs/remotes 静默失败 —— 与 helloai 实况一致）")

# 引用已存在但内容是过期旧值时必须被修正——只看"存在性"会误判成功
ref_file.write_text("0" * 40 + "\n", encoding="ascii")
mode2 = G.ensure_remote_ref(repo3, "origin", "main", str(bare3),
                            G.git_prefix("gitee.com"), G.net_env())
_check_now = ref_file.read_text().strip()
check("过期引用被修正为 HEAD（没被误判成功）",
      _check_now == head3, f"mode={mode2!r} 内容={_check_now[:12]}")

print()
print("=" * 60)
print("7) resolve_remotes —— 复用已有远端要按「地址」判断，不能只认名字")
print("=" * 60)
rm = G.remote_matches
check("https 带 .git 能认", rm("https://github.com/u/helloai.git", "github", "helloai"))
check("https 不带 .git 能认", rm("https://gitee.com/u/helloai", "gitee", "helloai"))
check("SSH 简写能认", rm("git@gitee.com:u/helloai.git", "gitee", "helloai"))
check("带账号前缀的 https 能认", rm("https://u@github.com/u/helloai.git", "github", "helloai"))
check("尾斜杠能认", rm("https://gitee.com/u/helloai/", "gitee", "helloai"))
check("大小写不敏感", rm("https://github.com/U/HelloAI.git", "github", "helloai"))
check("平台不对要拒绝", not rm("https://gitee.com/u/helloai.git", "github", "helloai"))
check("仓库名只是前缀不算命中（helloai ≠ helloai-demo）",
      not rm("https://gitee.com/u/helloai-demo.git", "gitee", "helloai"))
check("别人账号下的同名仓库不算命中（给了 login 时）",
      not rm("https://gitee.com/other/helloai.git", "gitee", "helloai", "zhangsan"))
check("属主一致才算命中",
      rm("https://gitee.com/zhangsan/helloai.git", "gitee", "helloai", "zhangsan"))

# 真实场景复刻：Gitee 仓库叫 helloai-demo、GitHub 仓库叫 helloai，
# 然后单跑 --platform github —— 必须复用 mirror，
# 而不是像旧逻辑那样又加一个 github-remote。
repo4 = BASE / "reprepo"
repo4.mkdir(parents=True, exist_ok=True)
sh(["git", "init", "-q", "-b", "main"], cwd=repo4)
sh(["git", "remote", "add", "origin",
    "https://gitee.com/zhangsan/helloai-demo.git"], cwd=repo4)
sh(["git", "remote", "add", "mirror",
    "https://github.com/lisi/helloai.git"], cwd=repo4)
cfg4 = {"_repo": "helloai",
        "logins": {"gitee": "zhangsan", "github": "lisi"}}
plan4 = G.resolve_remotes(repo4, ["github"], "https", dict(cfg4), SimpleNamespace())
remotes4 = sh(["git", "remote"], cwd=repo4).stdout.split()
check("单跑 github 复用了已有 mirror",
      plan4 == [("github", "mirror")], f"plan={plan4}")
check("没有多出第三个远端", sorted(remotes4) == ["mirror", "origin"], str(remotes4))

# 两边仓库名一致时，双平台各复用各的，且不新增远端
repo5 = BASE / "reprepo5"
repo5.mkdir(parents=True, exist_ok=True)
sh(["git", "init", "-q", "-b", "main"], cwd=repo5)
sh(["git", "remote", "add", "origin",
    "https://gitee.com/zhangsan/helloai.git"], cwd=repo5)
sh(["git", "remote", "add", "mirror",
    "https://github.com/lisi/helloai.git"], cwd=repo5)
plan5 = G.resolve_remotes(repo5, ["gitee", "github"], "https", dict(cfg4),
                          SimpleNamespace())
remotes5 = sh(["git", "remote"], cwd=repo5).stdout.split()
check("双平台各复用 origin / mirror",
      plan5 == [("gitee", "origin"), ("github", "mirror")], f"plan={plan5}")
check("双平台跑完远端数量没变", sorted(remotes5) == ["mirror", "origin"], str(remotes5))

print()
print("=" * 60)
print("8) staged_deletions —— 删除也是改动，别报成「合计 0 B」")
print("=" * 60)
(repo3 / "b.txt").write_text("bye\n", encoding="utf-8")
sh(["git", "add", "b.txt"], cwd=repo3)
sh(["git", "-c", "user.email=t@e.com", "-c", "user.name=T",
    "commit", "-qm", "add b"], cwd=repo3)
(repo3 / "b.txt").unlink()
sh(["git", "add", "-A"], cwd=repo3)
dele = G.staged_deletions(repo3)
check("删除被识别出来", dele == ["b.txt"], str(dele))
rep = G.scan_staged(repo3, G.staged_files(repo3))
check("体检报告带 deleted 字段", rep.get("deleted") == ["b.txt"],
      str(rep.get("deleted")))
check("删除项体积为 0（所以必须单独列出）", rep["total_bytes"] == 0,
      str(rep["total_bytes"]))

print()
print("=" * 60)
print("9) github_ips —— 「绑 IP」这一级：DNS 优先，且必须给多个候选")
print("=" * 60)
# 2026-09-23 两次实测踩到的：
#   ① 写死的 140.82.11x.4（美国段）优先级高于 DNS，而本机解析到
#      20.205.243.166（亚太段）——绑老段不兜底，还白等一次超时。
#   ② 改成 DNS 优先后又只剩**一个**候选（dns_ips[:1]），结果当天 DNS 给的
#      20.205.243.166 恰好挂住不通，整级就废了、报"绑 IP 仍失败"——
#      同链路 140.82.121.4 明明是通的，只是从没被试到。
# 现在钉死：DNS 排最前 + 叠上候选段（去重、限量）。
ips = G.github_ips()
check("返回非空", bool(ips), str(ips))
check("候选个数有上限（总预算不能被无用段吃光）", len(ips) <= 4, str(ips))
check("候选列表无重复", len(ips) == len(set(ips)), str(ips))
check("反复调用结果稳定（不是随机挑）", G.github_ips() == ips, f"{ips}")

try:
    dns = [i[4][0] for i in socket.getaddrinfo("github.com", 443)]
except OSError:
    dns = []
if dns:
    check("DNS 结果排在最前（正常情况下 DNS 就是最优解）", ips[0] in dns, f"{ips} dns={dns}")
    check("DNS 之后仍叠上候选段（只试一个 = 只有一次机会）",
          any(ip in G.GITHUB_FALLBACK_IPS for ip in ips), f"{ips}")
else:
    check("DNS 拿不到时退回候选段",
          ips == list(G.GITHUB_FALLBACK_IPS)[:len(ips)], f"{ips}")

_real_run, _real_out, _real_sha = G.run, G.git_out, G.read_remote_sha
cmds = []


def fake_run(cmd, **kw):
    cmds.append(list(cmd))
    return SimpleNamespace(returncode=1, stdout="", stderr=(
        "fatal: unable to access 'https://github.com/x/y.git/': "
        "Failed to connect to github.com:443 after 21057 ms"))


G.run = fake_run
G.read_remote_sha = lambda *a, **k: ("", "boom")
G.git_out = lambda *a, **k: "a" * 40
pushed9, err9, _, _ = G.push_platform(
    repo3, "github", "mirror", "main", "lisi", "expotool", "tok",
    "https", SimpleNamespace(remember_credentials=False))
G.run, G.git_out, G.read_remote_sha = _real_run, _real_out, _real_sha

resolved = []
for c in cmds:
    for x in c:
        s = str(x)
        if s.startswith("http.curloptResolve="):
            resolved.append(s.rsplit(":", 1)[-1])
check("网络类错误会一路升级到绑 IP", bool(resolved), str(resolved))
check("绑的 IP 就是 github_ips() 给的（DNS 优先）",
      resolved == ips[:len(resolved)], f"{resolved} vs {ips}")
check("绑 IP 这一级会**逐个试多个**（回归：曾只试 1 个，DNS 给的 IP 一挂整级就废）",
      len(resolved) >= min(2, len(ips)), f"只试了 {resolved}")
check("绑 IP 用完了也没通就老实返回失败", pushed9 is False, f"{pushed9}")

# HTTP/1.1 兜底：2026-09-23 实测本机到 github.com 的 HTTP/2 被 RST，
# 切 HTTP/1.1 立刻握手成功。它必须**排在直连之后**，别拖慢正常路径。
flat = [str(x) for c in cmds for x in c]
check("阶梯里有 HTTP/1.1 那一级",
      any("http.version=HTTP/1.1" in s for s in flat), "")
first_cmd = [str(x) for x in cmds[0]] if cmds else []
check("第一级（直连）不带 HTTP/1.1，正常路径不受影响",
      not any("http.version" in s for s in first_cmd), f"{first_cmd}")
check("git_prefix(http1=True) 确实带上该参数",
      "http.version=HTTP/1.1" in G.git_prefix("github.com", None, True), "")
check("git_prefix 默认不带（不改变原有行为）",
      "http.version=HTTP/1.1" not in G.git_prefix("github.com"), "")

# ---------------------------------------------------------------------------
# §10 代理冒充「认证失败」——降级阶梯不能被假权限错误一级断死
# ---------------------------------------------------------------------------
# 2026-09-23 实测：本地代理回 `CONNECT tunnel failed, response 502`，
# git 却打印 `fatal: Authentication failed for 'https://github.com/...'`。
# 原因是**代理拒绝 CONNECT 时，git 会把 URL 里带的凭据拿去当「代理」凭据试**，
# 被拒后报的就是这句。
# 而"权限类错误立即停"这条规矩会把它当成"令牌没权限"→
# **阶梯在第 1 级就断，永远走不到「清代理」那一级**——那一级才是能用的。
AUTH_STDERR = ("fatal: Authentication failed for "
               "'https://github.com/lisi/expotool.git/'")
check("这条原话会被认成认证类错误", G.is_auth_error(AUTH_STDERR), AUTH_STDERR)
check("它不属于网络类错误（这正是被误判成权限问题的原因）",
      not G.is_network_error(AUTH_STDERR), "")


def run_push_with(stderr, install_proxy):
    """跑一次 push_platform，返回 (成功?, 错误文本, 发起的命令列表)。"""
    saved_env = {v: os.environ.get(v) for v in G.PROXY_VARS}
    _r, _o, _s, _ips = G.run, G.git_out, G.read_remote_sha, G.github_ips
    issued = []
    try:
        for v in G.PROXY_VARS:
            os.environ.pop(v, None)
        if install_proxy:
            os.environ["https_proxy"] = "http://127.0.0.1:7890"

        def fake_run(cmd, **kw):
            issued.append(list(cmd))
            return SimpleNamespace(returncode=1, stdout="", stderr=stderr)

        G.run = fake_run
        G.read_remote_sha = lambda *a, **k: ("", "boom")
        G.git_out = lambda *a, **k: "a" * 40
        # 钉死候选 IP，测试不打 DNS
        G.github_ips = lambda *a, **k: ["192.0.2.1", "192.0.2.2"]
        ok, err, _, _ = G.push_platform(
            repo3, "github", "mirror", "main", "lisi", "expotool", "tok",
            "https", SimpleNamespace(remember_credentials=False))
        return ok, err, issued
    finally:
        G.run, G.git_out, G.read_remote_sha, G.github_ips = _r, _o, _s, _ips
        for v in G.PROXY_VARS:
            os.environ.pop(v, None)
        for v, val in saved_env.items():
            if val is not None:
                os.environ[v] = val


ok_px, err_px, cmds_px = run_push_with(AUTH_STDERR, install_proxy=True)
ips_px = [str(x) for c in cmds_px for x in c
          if str(x).startswith("http.curloptResolve=")]
check("有代理时：认证失败**不再一级断死**，会继续升到绑 IP 那一级",
      bool(ips_px), f"只跑了 {len(cmds_px)} 级：{[str(c[0:3]) for c in cmds_px]}")
check("有代理时：结论仍是失败（继续试 ≠ 假装成功）", ok_px is False, f"{ok_px}")
check("有代理时：错误里必须说清「这条认证失败可能是代理冒充的」",
      "代理" in err_px and "冒充" in err_px, err_px[-90:])

ok_np, err_np, cmds_np = run_push_with(AUTH_STDERR, install_proxy=False)
check("没配代理时：认证失败仍是权限问题，立即停（不盲目重试）",
      len(cmds_np) == 1, f"跑了 {len(cmds_np)} 级")
check("没配代理时：不硬塞「代理冒充」这条解释（减少误报）",
      "冒充" not in err_np, err_np[-60:])
# 这条断言第一版写错了：我默认了"环境里没代理"。实际本机**本来就配着代理**
# （不然也不需要"清代理"这一级），于是还原环境后判定为真、测试红了。
# 正确写法是先显式清干净再断言，别依赖宿主环境。
_saved_proxy = {v: os.environ.get(v) for v in G.PROXY_VARS}
for v in G.PROXY_VARS:
    os.environ.pop(v, None)
check("清掉所有代理变量后 has_proxy_env() 为假（依据是环境，不是缓存）",
      G.has_proxy_env() is False, "")
os.environ["https_proxy"] = "http://127.0.0.1:7890"
check("set 一个代理变量后 has_proxy_env() 立刻为真",
      G.has_proxy_env() is True, "")
for v in G.PROXY_VARS:
    os.environ.pop(v, None)
for v, val in _saved_proxy.items():
    if val is not None:
        os.environ[v] = val

print()
print("=" * 60)
print("11) https 推送不顶掉已有的上游（2026-09-23 实测修）")
print("=" * 60)
# 背景：https 推送成功后会写 branch.<b>.remote/merge，目的是让 git status 能看到
# 远端对比（修早年的 [gone] 问题）。但它原来是**无条件覆盖**的——仓库若是从
# Gitee clone 下来的（本来就跟踪 origin），推一次 GitHub 就把上游改成 mirror，
# 用户之后裸跑 git pull 会静默跑去另一个平台。现在改成「已有上游就不动」。
# 用一个本地裸仓库冒充远端：把 auth_url 换掉，就能离线跑 https 这条路径。
_bareA = BASE / "bareA.git"
_bareB = BASE / "bareB.git"
_bareC = BASE / "bareC.git"
for _b in (_bareA, _bareB, _bareC):
    sh(["git", "init", "--bare", "-q", str(_b)])
_real_auth_url = G.auth_url


def _mk_repo(name):
    d = BASE / name
    d.mkdir()
    (d / "c.txt").write_text("x\n", encoding="utf-8")
    for cmd in (["git", "init", "-b", "main", "-q"],
                ["git", "config", "user.name", "T"],
                ["git", "config", "user.email", "t@e.com"],
                ["git", "add", "-A"],
                ["git", "commit", "-q", "-m", "first"]):
        sh(cmd, cwd=str(d), env=G.net_env())
    return d


# 11a) 已有上游 -> 必须保持不动
_r1 = _mk_repo("upstream_keep")
sh(["git", "remote", "add", "origin", str(_bareA)], cwd=str(_r1), env=G.net_env())
sh(["git", "push", "-q", "-u", "origin", "main:main"], cwd=str(_r1), env=G.net_env())
_before = sh(["git", "config", "--get", "branch.main.remote"], cwd=str(_r1)).stdout.strip()
check("前置：这个仓库本来就跟踪 origin", _before == "origin", f"up={_before!r}")

G.auth_url = lambda *a, **k: str(_bareB)      # https 路径改打本地裸仓库
_p1, _e1, _v1, _n1 = G.push_platform(_r1, "github", "mirror", "main",
                                     "u", "r", "t", "https", args)
G.auth_url = _real_auth_url
check("https 路径推到第二个远端成功", _p1, f"err={_e1[:120]!r}")
_after = sh(["git", "config", "--get", "branch.main.remote"], cwd=str(_r1)).stdout.strip()
check("已有上游 origin 没被顶成 mirror", _after == "origin", f"up={_after!r}")
_bsha, _ = G.read_remote_sha(_r1, G.git_prefix("github.com"), str(_bareB), "main", G.net_env())
check("第二个远端确实收到了这次提交", _bsha != "", f"sha={_bsha[:8]!r}")

# 11b) 没有上游 -> 照常建立（别把早年修的 [gone] 又弄回来）
_r2 = _mk_repo("upstream_fresh")
_fresh_before = sh(["git", "config", "--get", "branch.main.remote"],
                   cwd=str(_r2)).stdout.strip()
check("前置：新仓库没有上游", _fresh_before == "", f"up={_fresh_before!r}")
G.auth_url = lambda *a, **k: str(_bareC)      # 换一个干净的裸仓库，避免历史冲突
_p2, _e2, _v2, _n2 = G.push_platform(_r2, "gitee", "origin", "main",
                                     "u", "r", "t", "https", args)
G.auth_url = _real_auth_url
check("https 路径推送成功（新仓库）", _p2, f"err={_e2[:120]!r}")
_fresh_after = sh(["git", "config", "--get", "branch.main.remote"],
                  cwd=str(_r2)).stdout.strip()
check("没有上游时会建立（本次修复没把它弄坏）",
      _fresh_after == "origin", f"up={_fresh_after!r}")

print()
print("=" * 60)
print("12) 校验失败的原因不能被丢掉（2026-09-23 实测修）")
print("=" * 60)
# 实测现场：重推一个**已经同步好**的仓库时，push 回了 "Everything up-to-date"
# （读着像一切正常），而同一时刻代理抽风让 ls-remote 失败
# （CONNECT tunnel failed, response 502）。
# 脚本原来写的是 `remote_sha, _ = read_remote_sha(...)`——**把校验错误丢了**，
# 于是把 "Everything up-to-date" 当失败原因报出来；这句话不像网络错误，
# 阶梯在第 1 级就断死，「清代理」那几档一次都没试——而那几档恰恰治这个。
UP_TO_DATE = "Everything up-to-date"
PROXY_502 = ("fatal: unable to access "
             "'https://github.com/lisi/expotool.git/': "
             "CONNECT tunnel failed, response 502")
_vf_n = [0]


def push_with_verify_failing(verify_err, succeed_at=None):
    """push 输出看起来正常，但**校验阶段**失败；succeed_at 次尝试起才校验通过。"""
    _r, _o, _s, _ips = G.run, G.git_out, G.read_remote_sha, G.github_ips
    issued, state = [], {"n": 0}
    saved_env = {v: os.environ.get(v) for v in G.PROXY_VARS}
    try:
        for v in G.PROXY_VARS:
            os.environ.pop(v, None)
        os.environ["https_proxy"] = "http://127.0.0.1:7890"   # 现场就是配着代理

        def fake_run(cmd, **kw):
            issued.append(list(cmd))
            return SimpleNamespace(returncode=0, stdout="", stderr=UP_TO_DATE)

        def fake_sha(*a, **k):
            state["n"] += 1
            if succeed_at is not None and state["n"] >= succeed_at:
                return ("a" * 40, "")
            return ("", verify_err)

        G.run = fake_run
        G.read_remote_sha = fake_sha
        G.git_out = lambda *a, **k: "a" * 40
        G.github_ips = lambda *a, **k: ["192.0.2.1", "192.0.2.2"]
        _vf_n[0] += 1
        root = _mk_repo(f"verifyfail{_vf_n[0]}")
        return G.push_platform(root, "github", "mirror", "main",
                               "lisi", "expotool", "tok", "https", args), issued
    finally:
        G.run, G.git_out, G.read_remote_sha, G.github_ips = _r, _o, _s, _ips
        for v in G.PROXY_VARS:
            os.environ.pop(v, None)
        for v, val in saved_env.items():
            if val is not None:
                os.environ[v] = val


(_pa, _ea, _va, _na), _ia = push_with_verify_failing(PROXY_502)
_fa = [str(x) for c in _ia for x in c]
check("校验失败是网络类 → 会继续降级（回归：原来第 1 级就断死）",
      len(_ia) > 1, f"只跑了 {len(_ia)} 级")
check("一路升到「绑 IP」那一级",
      any("http.curloptResolve" in s for s in _fa), f"共 {len(_ia)} 级")
check("报出来的是**校验**失败原因，不是那句 Everything up-to-date",
      "CONNECT tunnel failed" in _ea and not _ea.startswith(UP_TO_DATE),
      f"err={_ea[:110]!r}")

(_pb, _eb, _vb, _nb), _ib = push_with_verify_failing(PROXY_502, succeed_at=2)
check("第 2 次尝试起校验通过 → 报告成功（「清代理」那级真能救回来）",
      _pb is True, f"err={_eb[:130]!r} 共跑了 {len(_ib)} 级")
check("救回来时报告了通道名", bool(_vb), f"via={_vb!r}")

print()
print("=" * 60)
print(f"结果：{len(passed)} 通过 / {len(failed)} 失败")
if failed:
    for f in failed:
        print("   FAIL:", f)
print("=" * 60)
rmtree_force(BASE)
sys.exit(1 if failed else 0)
