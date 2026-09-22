# -*- coding: utf-8 -*-
"""向导模式的离线测试。

两块：
  1) parse_account —— 用户会粘的各种账号地址写法
  2) wizard_collect —— 四问的取值、默认值复用、非法输入重问、已给参数不重复问

不联网、不写用户真实配置（CONFIG_PATH 指向临时目录）。
"""
import atexit
import json
import os
import shutil
import stat
import sys
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
    范围严格限制在自己这个前缀下。
    """
    now = time.time()
    for d in Path(tempfile.gettempdir()).glob(prefix + "*"):
        try:
            if d.is_dir() and now - d.stat().st_mtime > max_age:
                rmtree_force(d)
        except OSError:
            pass


passed, failed = [], []
WIZ_PREFIX = "gs_wiz_"
purge_stale(WIZ_PREFIX)
TMP = Path(tempfile.mkdtemp(prefix=WIZ_PREFIX))
atexit.register(rmtree_force, TMP)  # 中途异常 / sys.exit 也要清


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'[OK]  ' if cond else '[FAIL]'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 66)
print("1) parse_account —— 用户可能粘贴的各种写法")
print("=" * 66)
CASES = [
    ("https://gitee.com/zhangsan/", "gitee", "zhangsan", None),
    ("https://gitee.com/zhangsan", "gitee", "zhangsan", None),
    ("http://gitee.com/zhangsan/", "gitee", "zhangsan", None),
    ("gitee.com/zhangsan", "gitee", "zhangsan", None),
    ("www.gitee.com/zhangsan/", "gitee", "zhangsan", None),
    ("https://gitee.com/zhangsan/my-proj", "gitee", "zhangsan", "my-proj"),
    ("https://gitee.com/zhangsan/my-proj.git", "gitee", "zhangsan", "my-proj"),
    ("git@gitee.com:zhangsan/my-proj.git", "gitee", "zhangsan", "my-proj"),
    # 单段且以 .git 结尾 = SSH 简写，这一段是账号名而不是仓库名
    ("git@gitee.com:zhangsan.git", "gitee", "zhangsan", None),
    ("https://github.com/octocat", "github", "octocat", None),
    ("https://github.com/octocat/Hello-World.git", "github", "octocat", "Hello-World"),
    ("github.com/octocat/", "github", "octocat", None),
    ("git@github.com:octocat/repo.git", "github", "octocat", "repo"),
    ("https://gitee.com/zhangSan/", "gitee", "zhangSan", None),
    ("zhangsan", None, "zhangsan", None),
    ("  https://gitee.com/zhangsan/  ", "gitee", "zhangsan", None),
]
for raw, exp_p, exp_l, exp_r in CASES:
    p, lg, rp, err = G.parse_account(raw)
    okk = (p == exp_p and lg == exp_l and rp == exp_r)
    check(f"{raw!r}", okk, f"-> ({p}, {lg}, {rp}) 期望 ({exp_p}, {exp_l}, {exp_r})")

BAD = [
    ("", "空"),
    ("https://gitee.com/", "只有域名"),
    ("https://gitlab.com/foo", "站点不认识"),
    ("https://example.com/x", "站点不认识"),
]
for raw, why in BAD:
    p, lg, rp, err = G.parse_account(raw)
    check(f"拒绝 {raw!r}（{why}）", lg is None and bool(err), f"err={err!r}")

print()
print("=" * 66)
print("2) login_mismatch —— 地址账号与令牌账号是否冲突")
print("=" * 66)
check("一致 -> 不冲突", not G.login_mismatch("zhangsan", "zhangsan"))
check("大小写不同 -> 不算冲突", not G.login_mismatch("ZhangSan", "zhangsan"))
check("真的不同 -> 冲突", G.login_mismatch("zhangsan", "someoneelse"))
check("没给地址 -> 不判断", not G.login_mismatch(None, "someoneelse"))
check("令牌没返回账号 -> 不判断", not G.login_mismatch("zhangsan", ""))


# --------------------------------------------------------------------------
# 3) 向导取值
# --------------------------------------------------------------------------
def mkproj(name):
    """造一个测试用项目目录，返回绝对路径。"""
    p = TMP / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "main.py").write_text("print(1)\n", encoding="utf-8")
    return p


_CASE = [0]
ASKED_DEFAULTS = []   # [(prompt, default), ...]，用于断言"记住的值有没有变默认值"


def run_wizard(answers, secrets, bools, proj=None, args_over=None,
               cfg_seed=None, login="zhangsan"):
    """用脚本化的回答跑一遍向导，返回 (结果, 实际被问到的提示词列表, 项目目录)

    每次调用都用**独立的配置文件**（cfg_seed 可预置令牌），
    否则前一个用例存下的令牌会污染后一个用例。
    """
    _CASE[0] += 1
    cfg_path = TMP / f"cfg_{_CASE[0]}.json"
    cfg_path.write_text(json.dumps(cfg_seed or {}, ensure_ascii=False),
                        encoding="utf-8")
    proj = proj or mkproj("DefaultProj")

    asked = []
    del ASKED_DEFAULTS[:]
    a, s, b = list(answers), list(secrets), list(bools)

    def fake_ask(prompt, default="", yes_mode=False):
        asked.append(prompt)
        ASKED_DEFAULTS.append((prompt, default))
        return a.pop(0) if a else default

    def fake_secret(prompt, default=""):
        asked.append(prompt)
        ASKED_DEFAULTS.append((prompt, default))
        return s.pop(0) if s else default

    def fake_bool(prompt, default=True, yes_mode=False):
        asked.append(prompt)
        ASKED_DEFAULTS.append((prompt, default))
        return b.pop(0) if b else default

    def fake_login(platform, token):
        return (login, "") if login else ("", "bad token")

    G.INTERACTIVE = True
    G.CONFIG_PATH = cfg_path
    G.ask, G.ask_secret, G.ask_bool = fake_ask, fake_secret, fake_bool
    G.api_login = fake_login

    cfg = G.load_config()
    args = SimpleNamespace(dir=None, name=None, account=None, private=None,
                           public=False, platform=None, dry_run=False, yes=False,
                           desc="")
    for k, v in (args_over or {}).items():
        setattr(args, k, v)
    result = G.wizard_collect(args, cfg)
    return result, asked, proj


print()
print("=" * 66)
print("3) 正常四问：主页地址 + 令牌 + 文件夹 + 仓库名")
print("=" * 66)
p3 = mkproj("ProjThree")
r, asked, _ = run_wizard(
    answers=["https://gitee.com/zhangsan/", str(p3)],
    secrets=["tok-abc"],
    bools=[True],
    proj=p3,
)
check("平台解析为 gitee", r["platform"] == "gitee", f"{r['platform']!r}")
check("账号解析为 zhangsan", r["login"] == "zhangsan", f"{r['login']!r}")
check("文件夹就是回答的那个", Path(r["dir"]) == p3.resolve(), f"{r['dir']}")
check("仓库名默认取目录名", r["repo"] == "ProjThree", f"{r['repo']!r}")
check("私有 = True（回答 y）", r["private"] is True)
check("令牌被收下", r["token"] == "tok-abc")
# 四问 = ①账号 ②令牌 ③文件夹 ④仓库名；第 5 个提示是④之后的"私有/公开"确认
check("问了 4 项 + 1 个私有/公开确认", len(asked) == 5, f"问到：{asked}")

r2, _, _ = run_wizard(
    answers=["https://gitee.com/zhangsan/", str(p3), "my-custom-repo"],
    secrets=["tok-abc"], bools=[False], proj=p3,
)
check("手输仓库名时以手输为准", r2["repo"] == "my-custom-repo", f"{r2['repo']!r}")
check("公开（回答 n）", r2["private"] is False)

print()
print("=" * 66)
print("4) 地址里带了仓库名 -> 作为仓库名默认值")
print("=" * 66)
r3, _, _ = run_wizard(
    answers=["https://gitee.com/zhangsan/preset-name", str(p3)],
    secrets=["t"], bools=[True], proj=p3,
)
check("仓库名采用地址里的 preset-name", r3["repo"] == "preset-name", f"{r3['repo']!r}")

print()
print("=" * 66)
print("5) 非法输入要重问：坏地址、坏目录、空仓库名")
print("=" * 66)
r4, asked4, _ = run_wizard(
    answers=["https://gitlab.com/foo",
             "https://gitee.com/zhangsan/",
             str(TMP / "并不存在的目录"),
             str(p3),
             ""],
    secrets=["t"], bools=[True], proj=p3,
)
check("坏地址被挡后重问（共问了 2 次地址）",
      sum(1 for p in asked4 if p.startswith("①")) == 2, f"{asked4}")
check("坏目录被挡后重问",
      sum(1 for p in asked4 if p.startswith("③")) == 2, f"{asked4}")
check("空仓库名被挡后回落到默认值", r4["repo"] == "ProjThree", f"{r4['repo']!r}")
check("最终仍然拿到合法结果",
      r4["platform"] == "gitee" and Path(r4["dir"]) == p3.resolve())

print()
print("=" * 66)
print("5b) 只给用户名 -> 显式追问平台，不默默替你选")
print("=" * 66)
r4b, asked4b, _ = run_wizard(
    answers=["zhangsan", "github", str(p3)],
    secrets=["t"], bools=[True], proj=p3,
)
check("追问了平台", any("哪个平台" in p for p in asked4b), f"{asked4b}")
check("平台采用了回答的 github", r4b["platform"] == "github", f"{r4b['platform']!r}")
check("账号是 zhangsan", r4b["login"] == "zhangsan", f"{r4b['login']!r}")

r4c, asked4c, _ = run_wizard(
    answers=["zhangsan", "乱写的平台", "https://github.com/octocat/", str(p3)],
    secrets=["t"], bools=[True], proj=p3,
)
check("平台答错被挡、回到第①问",
      sum(1 for p in asked4c if p.startswith("①")) == 2, f"{asked4c}")
check("最终平台跟随第二次地址 github", r4c["platform"] == "github", f"{r4c['platform']!r}")

print()
print("=" * 66)
print("5c) 把账号地址错填进令牌那一问 -> 当场指出来")
print("=" * 66)
r4d, asked4d, _ = run_wizard(
    answers=["https://gitee.com/zhangsan/", str(p3)],
    secrets=["https://gitee.com/zhangsan/", "real-token"],
    bools=[True], proj=p3,
)
check("错误内容被挡（令牌那问问了 2 次）",
      sum(1 for p in asked4d if p.startswith("②")) == 2, f"{asked4d}")
check("最终用上了真正的令牌", r4d["token"] == "real-token", f"{r4d['token']!r}")

print()
print("=" * 66)
print("6) 空令牌要被挡（连给两次空才拿到有效令牌）")
print("=" * 66)
r5, _, _ = run_wizard(
    answers=["https://gitee.com/zhangsan/", str(p3)],
    secrets=["", "tok-real"], bools=[True], proj=p3,
)
check("第一个空令牌被忽略，用了后面那个", r5["token"] == "tok-real", f"{r5['token']!r}")

print()
print("=" * 66)
print("7) 命令已给的项不再重复问（--account / --dir / --name / --private）")
print("=" * 66)
r6, asked6, _ = run_wizard(
    answers=[], secrets=[], bools=[], proj=p3,
    args_over=dict(account="https://github.com/octocat/", dir=str(p3),
                   name="already-named", private=False),
    cfg_seed={"tokens": {"gitee": "", "github": "ghp_saved"}},
)
check("不再问账号", not any(p.startswith("①") for p in asked6), f"{asked6}")
check("不再问文件夹", not any(p.startswith("③") for p in asked6), f"{asked6}")
check("不再问仓库名", not any(p.startswith("④") for p in asked6), f"{asked6}")
check("不再问令牌（已存过）", not any(p.startswith("②") for p in asked6), f"{asked6}")
check("直接采用命令行给的值",
      r6["repo"] == "already-named" and r6["private"] is False
      and Path(r6["dir"]) == p3.resolve())
check("平台来自 --account", r6["platform"] == "github", f"{r6['platform']!r}")
check("令牌用了 github 那一枚", r6["token"] == "ghp_saved", f"{r6['token']!r}")

print()
print("=" * 66)
print("8) 已存过令牌 -> 回车即复用，不必重输")
print("=" * 66)
r7, asked7, _ = run_wizard(
    answers=["https://gitee.com/zhangsan/", str(p3)],
    secrets=[""], bools=[True], proj=p3,
    cfg_seed={"logins": {"gitee": "zhangsan"},
              "tokens": {"gitee": "saved-token", "github": ""}},
)
check("复用已保存的令牌", r7["token"] == "saved-token", f"{r7['token']!r}")
# 设计取舍：令牌已存过就不再问（日常重跑少一次输入）；要换令牌用 --set-token
check("已存过令牌就不再问那一问",
      not any(p.startswith("②") for p in asked7), f"{asked7}")
# 账号仍然问，但默认值就是记住的那个 —— 回车一下即可
acct_prompt = [d for p, d in ASKED_DEFAULTS if p.startswith("①")]
check("账号那一问仍出现（每次都可能换账号）",
      len(acct_prompt) == 1, f"{asked7}")
check("默认值就是记住的账号地址，回车即用",
      acct_prompt and acct_prompt[0] == "https://gitee.com/zhangsan/",
      f"{acct_prompt}")

print()
print("=" * 66)
print("9) 令牌校验失败不拦路（照走，只告警）")
print("=" * 66)
r8, _, _ = run_wizard(
    answers=["https://gitee.com/zhangsan/", str(p3)],
    secrets=["bad"], bools=[True], proj=p3, login="",
)
check("校验失败仍返回结果（不 die）", bool(r8["repo"]) and r8["token"] == "bad")

print()
print("=" * 66)
print("10) 集成：跑完整的 main()，看取值有没有真的流到建库和推送")
print("=" * 66)


def run_main(argv, login="zhangsan", created=(True, True, ""), extra_cfg=None):
    """带桩跑完整 main()：网络层、建库、推送全部替换，输出被收集。

    login 可以给 str，也可以给 {平台: 账号名} —— 两个平台账号名不同时要用后者。
    """
    msgs, calls = [], {}
    G.out = lambda m="": msgs.append(str(m))

    def fake_login(platform, token):
        want = login.get(platform, "") if isinstance(login, dict) else login
        return (want, "") if want else ("", "401 Access token does not exist")

    def fake_create(platform, token, name, private, desc):
        calls["create"] = dict(platform=platform, name=name, private=private)
        calls.setdefault("creates", []).append(dict(platform=platform, name=name,
                                                    private=private))
        return created

    def fake_resolve(root, live, proto, cfg, args):
        calls["live"] = list(live)
        return [(p, f"origin_{p}") for p in live]

    def fake_push(root, platform, remote, branch, login_, repo, token, proto, args):
        rec = dict(platform=platform, repo=repo, branch=branch, login=login_)
        calls["push"] = rec
        calls.setdefault("pushes", []).append(rec)
        return True, "", "直连", ""

    def fake_align(platform, owner, repo, token, private, is_new):
        calls["align"] = dict(platform=platform, owner=owner, repo=repo,
                              private=private, is_new=is_new)
        calls.setdefault("aligns", []).append(dict(platform=platform, owner=owner,
                                                   repo=repo, private=private,
                                                   is_new=is_new))
        return True, []

    def forbidden_http(*a, **k):
        """守卫：main() 全程不该再碰真实网络。

        踩过（2026-09-23）：给 main() 加了「建库后核对可见性」这一步之后，
        这里只桩了 api_create_repo，漏桩 align_visibility —— 于是测试拿假令牌
        去打了真的 gitee.com / api.github.com。**测试照样全绿**（因为请求失败后
        降级成告警），但已经不再是 hermetic 的：网络一抖结论就变，
        而且每次跑测试都在对外发请求。所以现在显式插一根钉子。
        """
        calls["http"] = calls.get("http", 0) + 1
        raise AssertionError("main() 试图访问真实网络 —— 有桩没打全")

    G.api_login = fake_login
    G.api_create_repo = fake_create
    G.align_visibility = fake_align
    G.http_json = forbidden_http
    G.resolve_remotes = fake_resolve
    G.push_platform = fake_push
    G.INTERACTIVE = False

    p = mkproj("MainProj")
    cp = TMP / f"maincfg_{len(msgs)}_{id(argv)}.json"
    cp.write_text(json.dumps({"identity": {"name": "T", "email": "t@e.com"},
                              **(extra_cfg or {})}, ensure_ascii=False),
                  encoding="utf-8")
    G.CONFIG_PATH = cp

    old = sys.argv
    sys.argv = ["git_sync.py", "--dir", str(p), "--yes"] + list(argv)
    code = 0
    try:
        G.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
    finally:
        sys.argv = old
    return msgs, calls, code, p


def has(msgs, needle):
    return any(needle in m for m in msgs)


msgs, calls, code, mp = run_main(
    ["--account", "https://gitee.com/someoneelse/"],
    extra_cfg={"tokens": {"gitee": "tok", "github": ""}})
check("账号不一致时给出明确提示", has(msgs, "账号对不上"), "")
check("不一致时**不会**去建库", "create" not in calls, f"{calls.get('create')}")
check("不一致时不会推送", "push" not in calls)
check("不一致时提示里有纠正办法", has(msgs, "--account"), "")
check("提示里同时点明了两个账号名",
      has(msgs, "someoneelse") and has(msgs, "zhangsan"),
      f"{[m for m in msgs if '对不上' in m]}")

msgs2, calls2, code2, _ = run_main(
    ["--account", "https://gitee.com/zhangsan/", "--name", "my-repo"],
    extra_cfg={"tokens": {"gitee": "tok", "github": ""}})
check("一致时平台为 gitee", calls2.get("create", {}).get("platform") == "gitee",
      f"{calls2.get('create')}")
check("仓库名从 --name 传到了建库调用",
      calls2.get("create", {}).get("name") == "my-repo", f"{calls2.get('create')}")
check("私有标志传到了建库调用",
      calls2.get("create", {}).get("private") is True, f"{calls2.get('create')}")
check("live 平台列表非空", calls2.get("live") == ["gitee"], f"{calls2.get('live')}")
check("推送时用的是令牌解析出的账号",
      calls2.get("push", {}).get("login") == "zhangsan", f"{calls2.get('push')}")
check("推送的仓库名与建库一致",
      calls2.get("push", {}).get("repo") == "my-repo", f"{calls2.get('push')}")
check("推送前用 refs 核对过（提示出现）",
      has(msgs2, "git ls-remote 核对远端 refs"), "")
check("建库后做了可见性核对（这正是 Gitee 会骗人的那一步）",
      calls2.get("align", {}).get("repo") == "my-repo", f"{calls2.get('align')}")
check("可见性核对的属主用的是令牌解析出的账号，不是用户输入的地址",
      calls2.get("align", {}).get("owner") == "zhangsan", f"{calls2.get('align')}")
check("全新仓库才纠正可见性（is_new=True）",
      calls2.get("align", {}).get("is_new") is True, f"{calls2.get('align')}")
check("main() 全程没有碰真实网络（http_json 一次都没被调用）",
      "http" not in calls2, f"被调用了 {calls2.get('http')} 次")

msgs3, calls3, _, _ = run_main(
    ["--account", "https://gitee.com/zhangsan/", "--public"],
    extra_cfg={"tokens": {"gitee": "tok", "github": ""}})
check("--public 让建库调用拿到 private=False",
      calls3.get("create", {}).get("private") is False, f"{calls3.get('create')}")
check("--public 也传到了可见性核对这一关",
      calls3.get("align", {}).get("private") is False, f"{calls3.get('align')}")

msgs4, calls4, _, _ = run_main(
    ["--account", "https://github.com/octocat/"],
    login="octocat", extra_cfg={"tokens": {"gitee": "", "github": "ghp_x"}})
check("GitHub 账号走 github 建库",
      calls4.get("create", {}).get("platform") == "github", f"{calls4.get('create')}")

msgs5, calls5, _, _ = run_main(
    ["--account", "https://gitee.com/zhangsan/"],
    created=(True, False, ""), extra_cfg={"tokens": {"gitee": "tok", "github": ""}})
check("仓库已存在时提示「复用」而非报错",
      has(msgs5, "已存在，复用"), "")
check("已存在也照样推送", "push" in calls5)
check("已存在的仓库走到可见性核对待查时，is_new=False（不擅自改设置）",
      calls5.get("align", {}).get("is_new") is False, f"{calls5.get('align')}")

msgs6, calls6, _, _ = run_main(
    ["--account", "https://gitee.com/zhangsan/"],
    created=(False, False, "HTTP 422: 当前账户尚未认证身份"),
    extra_cfg={"tokens": {"gitee": "tok", "github": ""}})
check("建库失败时给实名认证提示", has(msgs6, "实名认证"), "")
check("建库失败时不推送", "push" not in calls6)
check("建库失败时也不去查可见性（仓库都不存在）", "align" not in calls6,
      f"{calls6.get('align')}")

print()
print("=" * 66)
print("10b) 两个平台账号名不一样：必须按平台各自核对（否则 both 永远跑不起来）")
print("=" * 66)
# 常见情形：两个平台账号名并不是同一个（Gitee 是 zhangsan、GitHub 是 lisi）。
# 旧版本 expect_login 是**单值**，--platform both 时必然有一个平台被误判成
# "账号对不上"而被跳过 —— 于是"一次推两个平台"根本不可能成功。
dual_login = {"gitee": "zhangsan", "github": "lisi"}
dual_tok = {"tokens": {"gitee": "tok-g", "github": "ghp_h"}}

msgs7, calls7, _, _ = run_main(
    ["--platform", "both",
     "--account", "https://gitee.com/zhangsan/",
     "--account", "https://github.com/lisi/",
     "--name", "dual"],
    login=dual_login, extra_cfg=dual_tok)
created7 = [c["platform"] for c in calls7.get("creates", [])]
pushed7 = [c["platform"] for c in calls7.get("pushes", [])]
logins7 = [c["login"] for c in calls7.get("pushes", [])]
check("两个平台都没被判成「账号对不上」", not has(msgs7, "账号对不上"),
      f"{[m for m in msgs7 if '对不上' in m]}")
check("两个平台都建了库", created7 == ["gitee", "github"], f"{created7}")
check("两个平台都推了", pushed7 == ["gitee", "github"], f"{pushed7}")
check("各自用自己账号推", logins7 == ["zhangsan", "lisi"],
      f"{logins7}")
check("仓库名两边一致（--name 是全局的）",
      {c["name"] for c in calls7.get("creates", [])} == {"dual"},
      f"{calls7.get('creates')}")
check("进推送的平台列表含两个", calls7.get("live") == ["gitee", "github"],
      f"{calls7.get('live')}")

# 只给一个平台的地址时：另一个平台告警"跳过核对"，而不是被拦下
msgs8, calls8, _, _ = run_main(
    ["--platform", "both", "--account", "https://gitee.com/zhangsan/",
     "--name", "dual2"],
    login=dual_login, extra_cfg=dual_tok)
pushed8 = [c["platform"] for c in calls8.get("pushes", [])]
check("没给地址的平台只告警不拦", has(msgs8, "跳过账号归属核对"), "")
check("没给地址的平台照样推", pushed8 == ["gitee", "github"], f"{pushed8}")

# 给了两个平台的地址但有一个对不上 -> 只拦对不上的那个，另一个照推
msgs9, calls9, _, _ = run_main(
    ["--platform", "both",
     "--account", "https://gitee.com/zhangsan/",
     "--account", "https://github.com/wrong-owner/",
     "--name", "dual3"],
    login=dual_login, extra_cfg=dual_tok)
pushed9 = [c["platform"] for c in calls9.get("pushes", [])]
check("对不上的平台被拦下", has(msgs9, "github 账号对不上"), "")
check("对的上的平台不受牵连（照样推 gitee）", pushed9 == ["gitee"], f"{pushed9}")

print()
print("=" * 66)
print("11) 首次使用：先自我介绍，再问四件事")
print("=" * 66)

m_i, c_i, code_i, _ = run_main(["--intro"])
check("--intro 正常退出", code_i == 0, f"code={code_i}")
check("讲了能自动建库（用户不用先去网页建空库）",
      has(m_i, "你不用先去网页建一个空库"), "")
check("四项要提供的东西都列了",
      all(has(m_i, x) for x in ("① 账号主页地址", "② 私人令牌",
                                "③ 要上传的文件夹", "④ 仓库叫什么")), "")
check("说了令牌去哪拿、勾什么权限",
      has(m_i, "私人令牌") and has(m_i, "projects"), "")
check("承诺令牌不进仓库/不回显",
      has(m_i, "不回显") and has(m_i, "git-sync.json"), "")
check("承诺会核对账号归属", has(m_i, "核对"), "")
check("--intro 纯只读：不建库、不推送",
      "create" not in c_i and "push" not in c_i, f"{c_i}")

G.CONFIG_PATH = TMP / "firstrun.json"
if G.CONFIG_PATH.exists():
    G.CONFIG_PATH.unlink()
check("空配置 => 判为首次使用", G.is_first_run(G.load_config()) is True)
_c = G.load_config()
_c["tokens"]["gitee"] = "t"
G.save_config(_c)
check("存过令牌 => 不再是首次", G.is_first_run(G.load_config()) is False)

print()
print("=" * 66)
print(f"结果：{len(passed)} 通过 / {len(failed)} 失败")
for f in failed:
    print("   FAIL:", f)
print("=" * 66)
rmtree_force(TMP)
sys.exit(1 if failed else 0)
