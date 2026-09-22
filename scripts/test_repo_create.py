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
print(f"结果：{len(passed)} 通过 / {len(failed)} 失败")
for f in failed:
    print("   FAIL:", f)
print("=" * 64)
sys.exit(1 if failed else 0)
