# -*- coding: utf-8 -*-
"""远程建库路径的离线测试。

用假的 HTTP 层替换 http_json，验证 api_create_repo / api_login 真正发出的
URL、方法、请求体是什么，以及三种结局（新建 / 已存在复用 / 失败）的判定。

不联网、不消耗任何令牌。
"""
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL))
import git_sync as G  # noqa: E402

passed, failed = [], []
REQUESTS = []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'[OK]  ' if cond else '[FAIL]'} {name}" + (f"  {detail}" if detail else ""))


class FakeResponse:
    """按 URL 关键字返回预设响应，并记录收到的请求。"""

    def __init__(self, rules):
        self.rules = rules

    def __call__(self, url, method="GET", body=None, form=None, token=None):
        REQUESTS.append(dict(url=url, method=method, body=body, form=form, token=token))
        for matcher, resp in self.rules:
            if matcher(url, method):
                return resp
        return 500, {"message": "no rule matched"}, "no rule matched"

    def last(self):
        return REQUESTS[-1]


# --------------------------------------------------------------------------
print("=" * 64)
print("1) Gitee 新建仓库：请求该长什么样")
print("=" * 64)
fake = FakeResponse([
    (lambda u, m: u.endswith("/user") and m == "GET", (200, {"login": "alice"}, "")),
    (lambda u, m: u.endswith("/user/repos") and m == "POST",
     (201, {"full_name": "alice/demo", "ssh_url": "git@gitee.com:alice/demo.git"}, "")),
])
G.http_json = fake

login, err = G.api_login("gitee", "TOKEN123")
check("登录返回用户名", login == "alice", f"login={login!r} err={err!r}")
rq = fake.last()
check("登录用 GET /api/v5/user", rq["url"] == "https://gitee.com/api/v5/user" and rq["method"] == "GET")
check("Gitee 令牌走 form 而非 header",
      rq["form"] == {"access_token": "TOKEN123"}, f"{rq['form']}")

created, is_new, err = G.api_create_repo("gitee", "TOKEN123", "demo", True, "我的测试仓库")
rq = fake.last()
check("建库返回「成功且是新建」", created and is_new and err == "", f"{created},{is_new},{err!r}")
check("建库用 POST /api/v5/user/repos",
      rq["url"] == "https://gitee.com/api/v5/user/repos" and rq["method"] == "POST")
check("同时传 name 和 path（Gitee 两者都要）",
      rq["form"].get("name") == "demo" and rq["form"].get("path") == "demo",
      f"name={rq['form'].get('name')!r} path={rq['form'].get('path')!r}")
check("auto_init=False（建真正空仓库，否则首推会被拒）",
      rq["form"].get("auto_init") is False, f"auto_init={rq['form'].get('auto_init')!r}")
check("private=True 被传出", rq["form"].get("private") is True)
check("description 被传出", rq["form"].get("description") == "我的测试仓库")

# 表单实际编码后的样子（http_json 会把 bool 转成 "true"/"false" 字符串）
import urllib.parse as _up  # noqa: E402
payload = {k: ("true" if v is True else "false" if v is False else str(v))
           for k, v in rq["form"].items() if v is not None}
encoded = _up.urlencode(payload)
check("编码成 x-www-form-urlencoded 后的实际形态",
      "auto_init=false" in encoded and "private=true" in encoded, encoded)

# --------------------------------------------------------------------------
print()
print("=" * 64)
print("2) 仓库已存在 -> 复用，不报错")
print("=" * 64)
# Gitee 的错误体是嵌套的 {"error": {"base": ["..."]}}
G.http_json = FakeResponse([
    (lambda u, m: m == "POST",
     (400, {"error": {"base": ["已存在同地址仓库(忽略大小写)"]}},
      '{"error":{"base":["已存在同地址仓库(忽略大小写)"]}}')),
])
created, is_new, err = G.api_create_repo("gitee", "T", "demo", True, "")
check("判定为「已存在 -> 复用」而不是失败",
      created and not is_new and err == "", f"{created},{is_new},{err!r}")

print()
print("=" * 64)
print("3) 令牌/配额不足 -> 明确失败并带上平台原话")
print("=" * 64)
G.http_json = FakeResponse([
    (lambda u, m: m == "POST",
     (403, {"error": {"base": ["您的账户已被限制创建仓库"]}}, "")),
])
created, is_new, err = G.api_create_repo("gitee", "T", "demo", True, "")
check("判定为失败", not created)
check("错误信息里保留了平台原话（嵌套 error 被展开）",
      "已被限制创建仓库" in err, f"err={err!r}")

# --------------------------------------------------------------------------
print()
print("=" * 64)
print("4) GitHub 新建仓库：走 JSON body + Bearer")
print("=" * 64)
G.http_json = FakeResponse([
    (lambda u, m: u.endswith("/user") and m == "GET", (200, {"login": "bob"}, "")),
    (lambda u, m: u.endswith("/user/repos") and m == "POST",
     (201, {"full_name": "bob/demo"}, "")),
])
fake = G.http_json
login, err = G.api_login("github", "ghp_XXX")
check("登录返回用户名", login == "bob", f"login={login!r} err={err!r}")
check("GitHub 令牌走 Authorization header，不是 form",
      fake.last()["token"] == "ghp_XXX" and fake.last()["form"] is None)

created, is_new, err = G.api_create_repo("github", "ghp_XXX", "demo", False, "desc")
rq = fake.last()
check("建库返回「成功且是新建」", created and is_new and err == "", f"{created},{is_new},{err!r}")
check("用 POST api.github.com/user/repos",
      rq["url"] == "https://api.github.com/user/repos" and rq["method"] == "POST")
check("走 JSON body 而非 form", rq["body"] is not None and rq["form"] is None, f"body={rq['body']}")
check("body 含 name / private / auto_init",
      rq["body"].get("name") == "demo" and rq["body"].get("private") is False
      and rq["body"].get("auto_init") is False, f"{rq['body']}")
check("GitHub 不需要 path 字段（Gitee 独有）", "path" not in (rq["body"] or {}))

print()
print("=" * 64)
print("5) GitHub 已存在（422）与令牌无效（401）")
print("=" * 64)
G.http_json = FakeResponse([
    (lambda u, m: m == "POST",
     (422, {"message": "Repository creation failed.",
            "errors": [{"message": "name already exists on this account"}]}, "")),
])
created, is_new, err = G.api_create_repo("github", "T", "demo", False, "")
check("422 判定为「已存在 -> 复用」", created and not is_new, f"{created},{is_new},{err!r}")

G.http_json = FakeResponse([
    (lambda u, m: m == "POST", (401, {"message": "Bad credentials"}, "")),
])
created, is_new, err = G.api_create_repo("github", "T", "demo", False, "")
check("401 判定为失败", not created)
check("错误信息含 Bad credentials", "Bad credentials" in err, f"err={err!r}")

print()
print("=" * 64)
print("6) 关键边界：Gitee 的 422 实名认证报错不能被误判成「仓库已存在」")
print("=" * 64)
# 实测原话：422 {"error":{"base":["当前账户尚未认证身份，请通过「个人设置 - 帐号信息」下完成身份认证后再操作"]}}
real_422 = ("当前账户尚未认证身份，请通过「个人设置 - 帐号信息」下完成身份认证后再操作")
G.http_json = FakeResponse([
    (lambda u, m: m == "POST", (422, {"error": {"base": [real_422]}}, "")),
])
created, is_new, err = G.api_create_repo("gitee", "T", "demo", True, "")
check("Gitee 422 实名报错 -> 判定为失败（不是「已存在」）",
      not created and not is_new, f"created={created} is_new={is_new}")
check("原话被完整保留", real_422 in err, f"err={err!r}")
hint = G.create_error_hint(err)
check("给出实名认证的处置提示",
      bool(hint) and "实名认证" in " ".join(hint), f"{hint}")
check("提示里指明了去哪里操作", "帐号信息" in " ".join(hint))

print()
print("=" * 64)
print("7) create_error_hint 的分类")
print("=" * 64)
h = G.create_error_hint("HTTP 403: 您的令牌缺少 projects 权限")
check("projects 权限类 -> 权限提示", bool(h) and "权限不足" in " ".join(h), f"{h}")
h = G.create_error_hint("HTTP 401: Bad credentials")
check("401 -> 令牌无效提示", bool(h) and "令牌无效" in " ".join(h), f"{h}")
h = G.create_error_hint("HTTP 401: 401 Unauthorized: Access token does not exist")
check("Gitee 401 原话 -> 令牌无效提示", bool(h) and "令牌无效" in " ".join(h), f"{h}")
h = G.create_error_hint("HTTP 500: 服务器炸了")
check("无法归类 -> 返回空（不瞎猜）", h == [], f"{h}")

print()
print("=" * 64)
print("8) 建库可见性核对（Gitee 静默忽略 private → 必须读回来核对 + PATCH 纠正）")
print("=" * 64)
print("  背景：实测 Gitee 建库接口无视 private（5 种写法全建成私有，且返回 201）。")
print("  所以「建完读回来核对」是唯一可靠路径，这一节就是在钉死它。")


def fresh(rules):
    REQUESTS.clear()
    G.http_json = FakeResponse(rules)
    return REQUESTS


def is_get_repo(u, m):
    return m == "GET" and "/repos/alice/demo" in u


def is_patch_repo(u, m):
    return m == "PATCH" and u.split("?")[0].endswith("/repos/alice/demo")


# --- 8a 请求公开、建出来是私有 -> 必须自动 PATCH 成公开 ---
rq = fresh([
    (is_get_repo, (200, {"full_name": "alice/demo", "private": True}, "")),
    (is_patch_repo, (200, {"full_name": "alice/demo", "private": False}, "")),
])
okf, notes = G.align_visibility("gitee", "alice", "demo", "T", False, is_new=True)
check("8a 建出来是私有、请求公开 -> 判定为已纠正", okf is True, f"{okf} {notes}")
patches = [r for r in REQUESTS if r["method"] == "PATCH"]
check("8a 确实发了 PATCH", len(patches) == 1, f"{len(patches)} 次")
if patches:
    f = patches[0]["form"] or {}
    check("8a PATCH 用 form 传 access_token", f.get("access_token") == "T", f"{f}")
    check("8a PATCH **必须带 name**（否则 Gitee 报 name is missing）",
          f.get("name") == "demo", f"{f}")
    check("8a PATCH **必须带 path**（同上）", f.get("path") == "demo", f"{f}")
    check("8a PATCH 把 private 传成 false", f.get("private") is False, f"{f}")
check("8a 提示里说明了「Gitee 会忽略该参数」",
      any("忽略" in n for n in notes), f"{notes}")

# --- 8b 请求私有、建出来就是私有 -> 不该发多余 PATCH ---
rq = fresh([(is_get_repo, (200, {"private": True}, ""))])
okf, notes = G.align_visibility("gitee", "alice", "demo", "T", True, is_new=True)
check("8b 可见性本来就对 -> 判定一致", okf is True, f"{okf}")
check("8b 不浪费请求：没有发 PATCH",
      not [r for r in REQUESTS if r["method"] == "PATCH"], f"{[r['method'] for r in REQUESTS]}")
check("8b 不啰嗦：没有多余提示", notes == [], f"{notes}")

# --- 8c 已存在的仓库可见性不符 -> 只报告，绝不擅自改 ---
rq = fresh([(is_get_repo, (200, {"private": True}, ""))])
okf, notes = G.align_visibility("gitee", "alice", "demo", "T", False, is_new=False)
check("8c 已存在的仓库：不发 PATCH（改别人的设置是大错）",
      not [r for r in REQUESTS if r["method"] == "PATCH"], f"{[r['method'] for r in REQUESTS]}")
check("8c 但会报告差异", any("已存在" in n and "私有" in n for n in notes), f"{notes}")
check("8c 说明白「不改动已存在的仓库设置」",
      any("不改动" in n for n in notes), f"{notes}")

# --- 8d 读不回来 -> 不猜、不发 PATCH、明确说读不到 ---
rq = fresh([(is_get_repo, (404, {"message": "Not Found"}, ""))])
okf, notes = G.align_visibility("gitee", "alice", "demo", "T", False, is_new=True)
check("8d 读不到可见性 -> 返回 False（不假装成功）", okf is False, f"{okf}")
check("8d 读不到时不发 PATCH", not [r for r in REQUESTS if r["method"] == "PATCH"])
check("8d 明确说「读不到」而不是静默", any("读不到" in n for n in notes), f"{notes}")

# --- 8e GitHub 走 JSON body，不是 form ---
rq = fresh([
    (is_get_repo, (200, {"private": True}, "")),
    (is_patch_repo, (200, {"private": False}, "")),
])
G.align_visibility("github", "alice", "demo", "ghp_T", False, is_new=True)
patch = [r for r in REQUESTS if r["method"] == "PATCH"][0]
check("8e GitHub 的 PATCH 走 JSON body", patch["body"] == {"private": False}, f"{patch['body']}")
check("8e GitHub 的 PATCH 不带 form", patch["form"] is None, f"{patch['form']}")
check("8e GitHub 的 PATCH 带 Bearer 令牌", bool(patch["token"]), f"{patch['token']!r}")

# --- 8f 纠正失败：要求私有却留成公开 -> 必须吼一声 ---
rq = fresh([
    (is_get_repo, (200, {"private": False}, "")),
    (is_patch_repo, (500, {"message": "boom"}, "boom")),
])
okf, notes = G.align_visibility("gitee", "alice", "demo", "T", True, is_new=True)
check("8f 纠正失败 -> 返回 False", okf is False, f"{okf}")
check("8f 报出失败原因", any("改成私有失败" in n for n in notes), f"{notes}")
check("8f **要求私有却仍是公开 -> 明确警示**",
      any("公开" in n and n.startswith("⚠️") for n in notes), f"{notes}")

print()
print("=" * 64)
print("9) --visibility：改**已存在**仓库的可见性（建库那条路故意不管已存在的库）")
print("=" * 64)
print("  背景：--public/--private 只纠正「刚建出来」的仓库；已存在的按约定不动。")
print("  要把现成的库转公开，只能走这个显式入口 —— 这一节钉死它的三道闸：")
print("  ① 改完必读回 ② 不 --yes 必须停下 ③ 转公开要问一次、默认不能是「是」。")


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class VisStub:
    """有状态的假远端：GET 读当前可见性，PATCH 改它。

    这一节要验的恰恰是「改完读回来」是否正确，所以不能像 FakeResponse 那样
    对同一条件永远返回同一个值 —— 那会把「没生效」和「生效了」测成一样。
    """

    def __init__(self, private, get_status=200, patch_status=200, patch_applies=True):
        self.private = private
        self.get_status = get_status
        self.patch_status = patch_status
        self.patch_applies = patch_applies   # 造「PATCH 返回 200 却没生效」的情形
        self.requests = []

    def __call__(self, url, method="GET", body=None, form=None, token=None):
        self.requests.append(dict(url=url, method=method, body=body, form=form, token=token))
        if method == "PATCH":
            if self.patch_status != 200:
                return self.patch_status, {"message": "boom"}, "boom"
            if self.patch_applies:
                raw = (form or body or {}).get("private")
                self.private = (raw is True) or (str(raw).lower() == "true")
            return 200, {"private": self.private}, ""
        if self.get_status != 200:
            return self.get_status, {"message": "Not Found"}, "Not Found"
        return 200, {"private": self.private}, ""

    def patches(self):
        return [r for r in self.requests if r["method"] == "PATCH"]

    def gets(self):
        return [r for r in self.requests if r["method"] == "GET"]


def cfg_of(gitee_token="T", gitee_login="alice", gh_token="", gh_login=""):
    return {"tokens": {"gitee": gitee_token, "github": gh_token},
            "logins": {"gitee": gitee_login, "github": gh_login},
            "identity": {}, "defaults": {"platforms": ["gitee"], "private": True}}


def vargs(**kw):
    d = dict(visibility="public", name="demo", dir=None, account=None,
             platform="gitee", yes=True, dry_run=False)
    d.update(kw)
    return NS(**d)


def capture(fn):
    """跑一遍并收走它会打印的东西 —— 有些结论只体现在话里。"""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fn()
    return code, buf.getvalue()


REAL_INTERACTIVE, REAL_ASK = G.INTERACTIVE, G.ask_bool
G.INTERACTIVE = False            # 测试环境必须是非交互，否则会真等人敲键盘

# --- 9a 已经是公开 -> 一个请求都不多发 ---
st = VisStub(private=False)
G.http_json = st
code, text = capture(lambda: G.run_visibility_mode(cfg_of(), vargs(visibility="public")))
check("9a 已经是要的状态 -> 成功码", code == G.SYNC_EXIT_IN_SYNC, f"{code}")
check("9a 不发多余 PATCH", st.patches() == [], f"{len(st.patches())} 次")
check("9a 只读了一次", len(st.gets()) == 1, f"{len(st.gets())} 次")

# --- 9b 私有的库转公开：要 PATCH，而且**必须读回来确认** ---
st = VisStub(private=True)
G.http_json = st
code, text = capture(lambda: G.run_visibility_mode(cfg_of(), vargs(visibility="public")))
check("9b 转公开成功", code == G.SYNC_EXIT_IN_SYNC, f"{code}")
check("9b 发了 PATCH", len(st.patches()) == 1, f"{len(st.patches())} 次")
if st.patches():
    f = st.patches()[0]["form"] or {}
    check("9b PATCH 带 name（Gitee 少了会 400 name is missing）", f.get("name") == "demo", f"{f}")
    check("9b PATCH 带 path", f.get("path") == "demo", f"{f}")
    check("9b PATCH 显式传 private=false", f.get("private") is False, f"{f}")
    check("9b 走的是 /repos/alice/demo",
          st.patches()[0]["url"].split("?")[0].endswith("/repos/alice/demo"),
          st.patches()[0]["url"])
check("9b ⭐ 改完读回来了（GET ≥ 2 次：改前 + 改后）", len(st.gets()) >= 2, f"{len(st.gets())} 次")
check("9b 远端状态真的变了", st.private is False)
check("9b 话里说清了「已确认为公开」", "已确认为公开" in text, text[-200:])

# --- 9c ⭐ PATCH 返回 200 却没生效 -> 绝不能当成功 ---
st = VisStub(private=True, patch_applies=False)
G.http_json = st
code, text = capture(lambda: G.run_visibility_mode(cfg_of(), vargs(visibility="public")))
check("9c 返回码不是 0（不假装成功）", code != G.SYNC_EXIT_IN_SYNC, f"{code}")
check("9c 明说「读回来仍是私有」", "仍是私有" in text, text[-260:])
check("9c 指向网页设置（人能手动补救）", "网页" in text)

# --- 9d 非交互 + 没 --yes -> 直接拒绝，一个都不动 ---
st = VisStub(private=True)
G.http_json = st
try:
    capture(lambda: G.run_visibility_mode(cfg_of(), vargs(visibility="public", yes=False)))
    code = None
except SystemExit as e:
    code = e.code
check("9d 非交互下没 --yes -> 拒掉（退出码 1）", code == 1, f"{code}")
check("9d ⭐ 拒绝时一个仓库都没动", st.patches() == [], f"{st.patches()}")

# --- 9e 交互式下用户答「否」-> 取消，也不动 ---
st = VisStub(private=True)
G.http_json = st
G.INTERACTIVE = True
G.ask_bool = lambda *a, **k: False
code, text = capture(lambda: G.run_visibility_mode(cfg_of(), vargs(visibility="public", yes=False)))
G.INTERACTIVE, G.ask_bool = REAL_INTERACTIVE, REAL_ASK
check("9e 用户拒绝 -> 正常收尾（不是错误退出）", code == G.SYNC_EXIT_IN_SYNC, f"{code}")
check("9e 用户拒绝 -> 不发 PATCH", st.patches() == [])
check("9e 用户拒绝 -> 明说「一个都没有改」", "一个都没有改" in text, text[-160:])

# --- 9e2 问句的默认值必须是「否」：回车不能把私库变公开 ---
prompts = []
G.INTERACTIVE = True
G.ask_bool = lambda prompt, default=True, yes_mode=False: (prompts.append(default), False)[1]
st = VisStub(private=True)
G.http_json = st
capture(lambda: G.run_visibility_mode(cfg_of(), vargs(visibility="public", yes=False)))
G.INTERACTIVE, G.ask_bool = REAL_INTERACTIVE, REAL_ASK
check("9e2 ⭐ 转公开的确认默认是「否」（回车 = 不改）", prompts == [False], f"{prompts}")

# --- 9f --dry-run：只说计划，不动手 ---
st = VisStub(private=True)
G.http_json = st
code, text = capture(lambda: G.run_visibility_mode(
    cfg_of(), vargs(visibility="public", dry_run=True)))
check("9f 演练不发 PATCH", st.patches() == [])
check("9f 演练不算失败", code == G.SYNC_EXIT_IN_SYNC, f"{code}")
check("9f 说明是演练、没改动", "演练" in text)
check("9f 仍会说出「私有 → 公开」的计划", "→" in text)

# --- 9g 仓库不存在 -> 停下，不猜、不改 ---
st = VisStub(private=True, get_status=404)
G.http_json = st
code, text = capture(lambda: G.run_visibility_mode(cfg_of(), vargs(visibility="public")))
check("9g 读不到 -> 不通的退出码（不是成功）", code == G.SYNC_EXIT_UNREACHABLE, f"{code}")
check("9g 读不到时不发 PATCH", st.patches() == [])
check("9g 说清「读不到」而不是当成公开", "读不到" in text)

# --- 9h 缺令牌/账号 -> 跳过并提示怎么配 ---
st = VisStub(private=True)
G.http_json = st
code, text = capture(lambda: G.run_visibility_mode(
    cfg_of(gh_token="", gh_login=""), vargs(platform="github")))
check("9h 缺令牌 -> 不算成功", code == G.SYNC_EXIT_NEEDS_HUMAN, f"{code}")
check("9h 一个请求都不发", st.requests == [], f"{st.requests}")
check("9h 提示 --set-token 怎么配", "--set-token" in text)

# --- 9i --name 给成路径 -> 早期就拦下（别去改一个不存在的仓库名） ---
try:
    capture(lambda: G.run_visibility_mode(cfg_of(), vargs(name="a/b")))
    code = None
except SystemExit as e:
    code = e.code
check("9i --name 是路径 -> 拦下", code == 1, f"{code}")

# --- 9j 没给 --name 时按 --dir 的目录名推断 ---
st = VisStub(private=True)
G.http_json = st
capture(lambda: G.run_visibility_mode(
    cfg_of(), vargs(name=None, dir="C:/tmp/somewhere/myproj")))
check("9j 按目录名推断出仓库名",
      any("/repos/alice/myproj" in r["url"] for r in st.requests),
      f"{[r['url'] for r in st.requests][:2]}")

# --- 9k 反方向也要能用（公开 -> 私有） ---
st = VisStub(private=False)
G.http_json = st
code, text = capture(lambda: G.run_visibility_mode(cfg_of(), vargs(visibility="private")))
check("9k 转私有成功", code == G.SYNC_EXIT_IN_SYNC, f"{code}")
check("9k 传了 private=true",
      bool(st.patches()) and (st.patches()[0]["form"] or {}).get("private") is True,
      f"{st.patches()[:1]}")
check("9k 状态变成私有", st.private is True)

# --- 9l GitHub 走 JSON body，不是 form ---
st = VisStub(private=True)
G.http_json = st
capture(lambda: G.run_visibility_mode(
    cfg_of(gh_token="ghp_X", gh_login="alice"), vargs(platform="github")))
p = st.patches()[0]
check("9l GitHub PATCH 走 body", p["body"] == {"private": False}, f"{p['body']}")
check("9l GitHub PATCH 不带 form", p["form"] is None, f"{p['form']}")
check("9l GitHub PATCH 带令牌", p["token"] == "ghp_X", f"{p['token']!r}")

# --- 9m 两个平台一次改完（各用各的账号） ---
st = VisStub(private=True)
G.http_json = st
code, text = capture(lambda: G.run_visibility_mode(
    cfg_of(gh_token="ghp_X", gh_login="alice"),
    vargs(platform="gitee,github")))
check("9m 两个平台都改了", len(st.patches()) == 2, f"{len(st.patches())} 次")
check("9m 一个平台一个地址",
      len(set(r["url"].split("?")[0] for r in st.patches())) == 2,
      f"{[r['url'].split('?')[0] for r in st.patches()]}")

# --- 9n ⭐ `--platform both` 必须展开成两个真平台名 ---
#   踩过（2026-10-03，推 douyin-publish 时）：这个入口**漏抄了主流程里那句展开**，
#   于是 "both" 被当成一个叫 both 的平台，在账号表里查不到 →
#   报出「both：缺账号，跳过」，把人往「去配令牌」的方向带，
#   而真实原因是参数根本没展开（现场给足了两个 --account）。
st = VisStub(private=True)
G.http_json = st
code, text = capture(lambda: G.run_visibility_mode(
    cfg_of(gh_token="ghp_X", gh_login="alice"), vargs(platform="both")))
check("9n --platform both 展开了（两个平台各发一次 PATCH）",
      len(st.patches()) == 2, f"{len(st.patches())} 次")
check("9n ⭐ 不再误报「both：缺账号」", "both：缺账号" not in text, text[-200:])
check("9n 打到两个不同仓库地址",
      len(set(r["url"].split("?")[0] for r in st.patches())) == 2,
      f"{[r['url'].split('?')[0] for r in st.patches()]}")

G.INTERACTIVE, G.ask_bool = REAL_INTERACTIVE, REAL_ASK
G.http_json = fake

print()
print("=" * 64)
print(f"结果：{len(passed)} 通过 / {len(failed)} 失败")
for f in failed:
    print("   FAIL:", f)
print("=" * 64)
sys.exit(1 if failed else 0)
