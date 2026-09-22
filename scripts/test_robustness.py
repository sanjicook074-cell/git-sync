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

print()
print("=" * 60)
print(f"结果：{len(passed)} 通过 / {len(failed)} 失败")
if failed:
    for f in failed:
        print("   FAIL:", f)
print("=" * 60)
rmtree_force(BASE)
sys.exit(1 if failed else 0)
