# -*- coding: utf-8 -*-
"""v1.12.0 新增「同步闭环」的测试：--status / --pull / --align。

原则和 test_robustness.py 一致：**桩化，不打真实网络、不碰真实远端**。
唯一用到真实 git 的地方是 cmd_pull 的快进合并——那是纯本地对象操作，
用一个临时仓库 + 两个提交来验，仍然是 hermetic 的。

这里刻意把「判断」和「执行」分开测：
  - cmd_align 的判断部分（哪些能推、哪些拒绝）不碰任何远端；
  - cmd_status 完全只读；
  - cmd_pull 只在快进那一步动本地分支，且必须在拉之前挡住"工作区不干净"。
"""
import atexit
import contextlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL))
import git_sync as G  # noqa: E402

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'[OK]  ' if cond else '[FAIL]'} {name}" + (f"  {detail}" if detail else ""))


def rmtree_force(path):
    """Windows 上 git 写的对象是只读的，得先去掉只读位（同 test_robustness）。"""
    def onerror(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass
    if Path(path).exists():
        shutil.rmtree(path, onerror=onerror)


BASE = Path(tempfile.mkdtemp(prefix="gs_sync_"))
atexit.register(rmtree_force, BASE)


def sh(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, env=G.net_env(), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def silent(fn, *a, **kw):
    """吞掉被测函数的打印。返回 **(函数返回值, 打印内容)** 二元组。

    ⚠️ 第一版注释写成"只拿返回值"，于是在 cmd_align 那节把返回的二元组
    当成了函数自己的返回值去解包（code, push_list = silent(...)），
    实际拿到的是 (code, 全部输出文本)——测试直接崩。
    **辅助函数的返回形状必须在名字或签名上说清楚，靠注释里的一句话不算。**
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


def align(ctx):
    """跑 cmd_align，返回 (退出码, 补推名单, 打印内容)。"""
    (code, push_list), text = silent(G.cmd_align, ctx)
    return code, push_list, text


def R(platform="gitee", relation="same", ahead=None, behind=None, reachable=True,
      remote_sha="a" * 40, login="zhangsan", repo="demoproj", via="直连", err="",
      token="tok"):
    """造一个 classify_platform 的结果。"""
    return {"platform": platform, "login": login, "repo": repo, "token": token,
            "reachable": reachable, "remote_sha": remote_sha, "via": via,
            "err": err, "relation": relation, "ahead": ahead, "behind": behind}


def ctx_of(results, sha="b" * 40, exists=True, dirty=None, root=None, branch="main"):
    return {"root": Path(root or BASE), "platforms": [r["platform"] for r in results],
            "proto": "https", "branch": branch, "repo": "demoproj", "targets": {},
            "entries": [], "results": results, "skipped": [],
            "local": {"exists": exists, "sha": sha, "dirty": list(dirty or [])}}


def rev(repo, ref="HEAD"):
    return sh(["git", "rev-parse", ref], cwd=str(repo)).stdout.strip()


# ===========================================================================
print("=" * 60)
print("1) classify_platform —— 八种关系都要分对")
print("=" * 60)

_REAL_PROBE, _REAL_FETCH, _REAL_GIT = (G.remote_branch_sha, G.fetch_branch, G.git)
LOCAL = "b" * 40


def cls(probe, fetch=("", "", "no"), revlist=None, local_sha=LOCAL, exists=True):
    G.remote_branch_sha = lambda *a, **k: probe
    G.fetch_branch = lambda *a, **k: fetch
    G.git = lambda *a, **k: SimpleNamespace(returncode=0, stdout=revlist or "",
                                            stderr="")
    try:
        return G.classify_platform(Path("."), "gitee", "zhangsan", "demoproj",
                                   "tok", "https", "main", local_sha, exists)
    finally:
        G.remote_branch_sha, G.fetch_branch, G.git = _REAL_PROBE, _REAL_FETCH, _REAL_GIT


r = cls((False, "", "", "CONNECT tunnel failed"))
check("连不上 → unreachable（而不是被当成'不一致'）",
      r["relation"] == "unreachable" and r["reachable"] is False, r["relation"])

r = cls((True, "", "直连", ""))
check("连上了但远端没这个分支 → missing（不能报成网络问题）",
      r["relation"] == "missing", r["relation"])

r = cls((True, "c" * 40, "直连", ""), exists=False)
check("本地还没提交 → local-empty", r["relation"] == "local-empty", r["relation"])

r = cls((True, LOCAL, "直连", ""))
check("SHA 相同 → same，且计数为 0",
      r["relation"] == "same" and r["ahead"] == 0 and r["behind"] == 0, str(r))

r = cls((True, "c" * 40, "直连", ""), fetch=(False, "", "fetch 挂了"))
check("SHA 不同但取不回对象 → unknown（不猜）", r["relation"] == "unknown", r["relation"])

r = cls((True, "c" * 40, "直连", ""), fetch=(True, "c" * 40, ""), revlist="0\t3")
check("本地领先 3 个 → ahead=3", r["relation"] == "ahead" and r["ahead"] == 3,
      f"{r['relation']} ahead={r['ahead']}")

r = cls((True, "c" * 40, "直连", ""), fetch=(True, "c" * 40, ""), revlist="2\t0")
check("本地落后 2 个 → behind=2", r["relation"] == "behind" and r["behind"] == 2,
      f"{r['relation']} behind={r['behind']}")

r = cls((True, "c" * 40, "直连", ""), fetch=(True, "c" * 40, ""), revlist="2\t3")
check("两边各有对方没有的 → diverged（这是能不能 pull 的分水岭）",
      r["relation"] == "diverged" and r["ahead"] == 3 and r["behind"] == 2, str(r))

check("--left-right 的左右顺序没有接反（左=落后、右=领先）",
      cls((True, "c" * 40, "直连", ""), fetch=(True, "c" * 40, ""),
          revlist="5\t1")["behind"] == 5, "left=5 必须是 behind")

# ===========================================================================
print()
print("=" * 60)
print("2) 显示宽度对齐（{:<20} 对中文会歪）")
print("=" * 60)


def disp_width(s):
    return sum(2 if G.unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in s)


check("pad 按显示宽度补齐（汉字算 2 列）", disp_width(G.pad("一致", 10)) == 10,
      f"{disp_width(G.pad('一致', 10))}")
check("pad 对纯 ASCII 等价于 str.ljust", G.pad("gitee", 10) == "gitee".ljust(10), "")
check("超长不截断（宁可歪一格，也不要丢信息）",
      G.pad("averyveryverylongname", 5) == "averyveryverylongname", "")
check("rel_text 会带上差几个提交",
      G.rel_text(R(relation="behind", behind=2)) == "本地落后 2 个提交",
      G.rel_text(R(relation="behind", behind=2)))
check("rel_text 对 same 不加数字", G.rel_text(R(relation="same")) == "一致", "")
check("rel_text 覆盖 missing（有专门措辞，不叫'不一致'）",
      G.rel_text(R(relation="missing")) == "远端还没有这个分支", "")

# ===========================================================================
print()
print("=" * 60)
print("3) cmd_status —— 只读，退出码必须能区分三种情况")
print("=" * 60)

code, text = silent(G.cmd_status, ctx_of([R(), R(platform="github")]))
check("全一致 + 工作区干净 → 0", code == G.SYNC_EXIT_IN_SYNC, f"{code}")
check("全一致时会明说无需操作", "无需操作" in text, text.strip()[-40:])

code, text = silent(G.cmd_status, ctx_of([R(relation="behind", behind=2)]))
check("本地落后 → 5（需要人介入）", code == G.SYNC_EXIT_NEEDS_HUMAN, f"{code}")
check("落后时点名建议 --pull", "--pull" in text, "")

code, text = silent(G.cmd_status, ctx_of([R(relation="ahead", ahead=1)]))
check("本地领先 → 5", code == G.SYNC_EXIT_NEEDS_HUMAN, f"{code}")
check("领先时点名建议 --align", "--align" in text, "")

code, text = silent(G.cmd_status,
                    ctx_of([R(reachable=False, relation="unreachable", err="timeout")]))
check("全平台拿不到 → 6（与'不一致'区分开）",
      code == G.SYNC_EXIT_UNREACHABLE, f"{code}")
check("拿不到时要说明这不是「不一致」", "没同步" in text or "未知" in text, "")

code, _ = silent(G.cmd_status, ctx_of([]))
check("一个平台都查不了 → 6", code == G.SYNC_EXIT_UNREACHABLE, f"{code}")

code, text = silent(G.cmd_status, ctx_of([R()], dirty=[" M a.txt"]))
check("远端一致但工作区脏 → 5，且措辞指向'提交'而不是'拉取'",
      code == G.SYNC_EXIT_NEEDS_HUMAN and "提交" in text, f"{code}")

code, text = silent(G.cmd_status,
                    ctx_of([R(), R(platform="github", reachable=False,
                                   relation="unreachable")]))
check("一半能查一半不能 → 不算全通（5）", code == G.SYNC_EXIT_NEEDS_HUMAN, f"{code}")

code, text = silent(G.cmd_status, ctx_of(
    [R(relation="diverged", ahead=1, behind=1)]))
check("分叉时明确说『不替你选 merge/rebase』", "rebase" in text, "")

# ===========================================================================
print()
print("=" * 60)
print("4) cmd_align —— 只补推确实落后的一方；落后/分叉一律拒绝")
print("=" * 60)

code, push_list, _t = align(ctx_of([R()]))
check("全一致 → 不推任何东西", push_list == [] and code == G.SYNC_EXIT_IN_SYNC,
      str(push_list))

code, push_list, _t = align(ctx_of([R(relation="ahead", ahead=3)]))
check("本地领先 → 进补推名单", [x["platform"] for x in push_list] == ["gitee"],
      str(push_list))

code, push_list, _t = align(ctx_of([R(relation="missing")]))
check("远端还没有这个分支 → 也可以推（首次推送该分支）",
      len(push_list) == 1, str(push_list))

code, push_list, _t = align(ctx_of([R(relation="behind", behind=4)]))
check("本地落后 → **拒绝补推**（推上去会盖掉远端的新提交）",
      push_list == [] and code == G.SYNC_EXIT_NEEDS_HUMAN, f"{code} {push_list}")

code, push_list, _t = align(ctx_of([R(relation="diverged", ahead=2, behind=1)]))
check("分叉 → 拒绝", push_list == [] and code == G.SYNC_EXIT_NEEDS_HUMAN, f"{code}")

code, push_list, _t = align(
    ctx_of([R(reachable=False, relation="unreachable", err="x")]))
check("拿不到远端 → 拒绝（不知道对面有什么，不能盲推）",
      push_list == [] and code == G.SYNC_EXIT_NEEDS_HUMAN, f"{code}")

code, push_list, _t = align(ctx_of([
    R(relation="ahead", ahead=1),
    R(platform="github", relation="behind", behind=2)]))
check("一个平台能推、另一个落后 → 只推能推的那个",
      [x["platform"] for x in push_list] == ["gitee"], str(push_list))

_, _, text = align(ctx_of([R(relation="behind", behind=1)]))
check("拒绝时说明'永远不 force push'（让人知道这是刻意的）",
      "force" in text, "")

# ===========================================================================
print()
print("=" * 60)
print("5) cmd_pull —— 只做快进；动之前先把危险情况挡住")
print("=" * 60)


def mk_repo():
    d = BASE / "pullrepo"
    if d.exists():
        rmtree_force(d)
    d.mkdir(parents=True)
    sh(["git", "init", "-b", "main"], cwd=str(d))
    sh(["git", "config", "user.name", "t"], cwd=str(d))
    sh(["git", "config", "user.email", "t@example.com"], cwd=str(d))
    (d / "a.txt").write_text("A\n", encoding="utf-8")
    sh(["git", "add", "-A"], cwd=str(d))
    sh(["git", "commit", "-m", "A"], cwd=str(d))
    sha_a = rev(d)
    (d / "b.txt").write_text("B\n", encoding="utf-8")
    sh(["git", "add", "-A"], cwd=str(d))
    sh(["git", "commit", "-m", "B"], cwd=str(d))
    sha_b = rev(d)
    sh(["git", "tag", "keepB", sha_b], cwd=str(d))     # 让 B 不被回收
    sh(["git", "reset", "--hard", sha_a], cwd=str(d))   # 本地退回 A
    return d, sha_a, sha_b


repo, SHA_A, SHA_B = mk_repo()
check("场景搭好：本地在 A，远端目标是它的后代 B",
      rev(repo) == SHA_A and SHA_A != SHA_B, f"{SHA_A[:8]} → {SHA_B[:8]}")

# ---- 工作区不干净：必须拒绝，且不能碰任何东西 ----
(repo / "a.txt").write_text("A modified by user\n", encoding="utf-8")
(repo / "untracked.txt").write_text("keep me\n", encoding="utf-8")
code, text = silent(G.cmd_pull, ctx_of(
    [R(relation="behind", behind=1, remote_sha=SHA_B)],
    sha=SHA_A, dirty=[" M a.txt"], root=repo))
check("工作区不干净 → 5，不动手", code == G.SYNC_EXIT_NEEDS_HUMAN, f"{code}")
check("工作区不干净时 HEAD 没有移动", rev(repo) == SHA_A, rev(repo)[:8])
check("工作区不干净时用户的改动还在（没被 stash / 没被丢）",
      (repo / "a.txt").read_text(encoding="utf-8") == "A modified by user\n"
      and (repo / "untracked.txt").exists(), "")
check("明说『不会替你 stash、不会丢弃改动』",
      "stash" in text, "")

sh(["git", "checkout", "--", "a.txt"], cwd=str(repo))
(repo / "untracked.txt").unlink()

# ---- 本地已最新：什么都不做 ----
code, _ = silent(G.cmd_pull, ctx_of([R()], sha=SHA_A, root=repo))
check("没有可拉的 → 0", code == G.SYNC_EXIT_IN_SYNC, f"{code}")
check("没可拉时 HEAD 没动", rev(repo) == SHA_A, "")

# ---- 分叉：拒绝，不动 ----
code, text = silent(G.cmd_pull, ctx_of(
    [R(relation="diverged", ahead=2, behind=1, remote_sha=SHA_B)],
    sha=SHA_A, root=repo))
check("分叉 → 5，不自动 merge", code == G.SYNC_EXIT_NEEDS_HUMAN, f"{code}")
check("分叉时 HEAD 没动（没有偷偷 rebase）", rev(repo) == SHA_A, "")
check("分叉时明确把选择权交回用户", "你的决定" in text, "")

# ---- 正常快进：真的把本地推进到 B ----
_REAL_FETCH2 = G.fetch_branch
G.fetch_branch = lambda *a, **k: (True, SHA_B, "")
try:
    code, text = silent(G.cmd_pull, ctx_of(
        [R(relation="behind", behind=1, remote_sha=SHA_B)],
        sha=SHA_A, root=repo))
finally:
    G.fetch_branch = _REAL_FETCH2
check("快进成功 → 0", code == G.SYNC_EXIT_IN_SYNC, f"{code}")
check("本地 HEAD 真的推进到了远端提交", rev(repo) == SHA_B,
      f"{rev(repo)[:8]} vs {SHA_B[:8]}")
check("远端带来的文件真的出现在工作区（不是只改了指针）",
      (repo / "b.txt").exists(), "")
check("报出了 from → to 与落后数", SHA_A[:8] in text and SHA_B[:8] in text, "")

# ---- 本地还没提交 ----
code, _ = silent(G.cmd_pull, ctx_of([R(relation="missing")], exists=False, root=repo))
check("本地还没提交 → 5（没有可拉的目标）", code == G.SYNC_EXIT_NEEDS_HUMAN, f"{code}")

# ---- 本地领先：不算要拉 ----
code, text = silent(G.cmd_pull, ctx_of([R(relation="ahead", ahead=1)], root=repo))
check("本地领先 → 0，并提示改推而不是拉",
      code == G.SYNC_EXIT_IN_SYNC and "--align" in text, f"{code}")

# ===========================================================================
print()
print("=" * 60)
print("6) 只读性：状态/对齐判断都不能改动本地仓库")
print("=" * 60)


def snapshot(d):
    files = {}
    for p in sorted(Path(d).rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            files[str(p.relative_to(d))] = p.read_bytes()
    return rev(d), files


before = snapshot(repo)
silent(G.cmd_status, ctx_of([R(relation="behind", behind=1)], sha=rev(repo), root=repo))
silent(G.cmd_align, ctx_of([R(relation="ahead", ahead=1)], sha=rev(repo), root=repo))
silent(G.cmd_align, ctx_of([R(relation="behind", behind=1)], sha=rev(repo), root=repo))
after = snapshot(repo)
check("连跑 status + align 判断后，HEAD 与工作区文件一字未变",
      before == after, f"{before[0][:8]} vs {after[0][:8]}")

# ===========================================================================
print()
print("=" * 60)
print("7) 测试夹具的键必须覆盖真实现（防「测试全绿、线上 KeyError」）")
print("=" * 60)

# 这次真踩了：classify_platform 忘了把 token 放进结果字典，
# 而夹具 R() 里也没有 token——**两边同时缺同一个字段，测试就永远发现不了**。
# 夹具是手写的，所以必须拿**真实现的输出**对一遍键，不能靠人记。
G.remote_branch_sha = lambda *a, **k: (True, "b" * 40, "直连", "")
G.fetch_branch = lambda *a, **k: (True, "b" * 40, "")
_QUIET = io.StringIO()
with contextlib.redirect_stdout(_QUIET):
    _real = G.classify_platform(Path("."), "gitee", "zhangsan", "demoproj",
                                "tok", "https", "main", "b" * 40, True)
G.remote_branch_sha, G.fetch_branch = _REAL_PROBE, _REAL_FETCH

check("夹具 R() 的键 ⊇ 真实现返回的键",
      set(R()) >= set(_real), f"缺：{sorted(set(_real) - set(R()))}")
check("夹具带着 token（cmd_pull 拼带凭据 URL 要用）",
      "token" in R(), "")
check("ctx_of() 与 sync_collect 的顶层键一致",
      set(ctx_of([R()])) >= {"root", "platforms", "proto", "branch", "repo",
                            "targets", "entries", "results", "skipped", "local"},
      "")

# ===========================================================================
print()
print("=" * 60)
print("8) align_execute —— 每个平台必须用「自己那个仓库名」匹配远端")
print("=" * 60)

# 2026-09-23 演练里真踩的：align_execute 原先用 ctx["repo"]（=**目录名**）当全局
# 仓库名去 resolve_remotes，而目录 drill 对应的远端仓库叫 git-sync-drill，
# 地址匹配不上 → 凭空新建了 `github-remote → .../drill.git`，还把
# branch.main.remote 指到了它上面。
# 最阴的地方是**推送本身是对的**（push_platform 用的是 r["repo"]），
# 所以屏幕上是"补推成功"，只有一行 `[OK] 远端 github-remote → .../drill.git` 露马脚，
# 结果是"推对了、remote 配错了"，用户之后裸跑 git push 会推到不存在的仓库。
_seen = []
_real_resolve, _real_push = G.resolve_remotes, G.push_platform


def _fake_resolve(root, platforms, proto, cfg, args):
    _seen.append(("resolve", platforms[0], cfg["_repo"]))
    return [(platforms[0], "origin" if platforms[0] == "gitee" else "mirror")]


def _fake_push(root, platform, remote, branch, login, repo, token, proto, args):
    _seen.append(("push", platform, remote, repo, login))
    return True, "", "直连", ""


G.resolve_remotes, G.push_platform = _fake_resolve, _fake_push
try:
    _ctx = ctx_of([R(relation="ahead", ahead=1, repo="git-sync-drill"),
                   R(platform="github", relation="ahead", ahead=1,
                     repo="git-sync-drill", login="lisi")])
    _ctx["repo"] = "drill"          # ← 目录名，故意给成错的
    _code, _txt = silent(G.align_execute, {"_repo": "drill", "logins": {}},
                         SimpleNamespace(), _ctx, _ctx["results"])
finally:
    G.resolve_remotes, G.push_platform = _real_resolve, _real_push

_resolve_seen = [c for c in _seen if c[0] == "resolve"]
_push_seen = [c for c in _seen if c[0] == "push"]
check("两个平台各 resolve 了一次（而不是一次算俩）",
      len(_resolve_seen) == 2, str(_resolve_seen))
check("resolve 用的是远端真名 git-sync-drill，**不是**目录名 drill",
      all(c[2] == "git-sync-drill" for c in _resolve_seen), str(_resolve_seen))
check("返回的远端名被真正用上了（origin / mirror）",
      [c[2] for c in _push_seen] == ["origin", "mirror"], str(_push_seen))
check("push 用的是每个平台自己的 repo（推对被推的仓库）",
      all(c[3] == "git-sync-drill" for c in _push_seen), str(_push_seen))
check("两个平台都推了", len(_push_seen) == 2, f"{_code}")

# ===========================================================================
print()
print("=" * 60)
print(f"结果：{len(passed)} 通过 / {len(failed)} 失败")
if failed:
    for f in failed:
        print("   FAIL:", f)
print("=" * 60)
rmtree_force(BASE)
sys.exit(1 if failed else 0)
