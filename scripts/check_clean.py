#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分发前自查：扫一遍技能目录，看有没有夹带不该带出去的东西。

为什么要有这个脚本
------------------
"我检查过了，很干净" 是一句无法复核的话。分享前把检查固化成命令，
下次（换人、换机器、换时间）跑同一句就能得到同一个结论。

设计取向：**宁可误报，不可漏报**。所有命中都只报告、不修改；
退出码非 0 表示"有东西要人来看一眼"，不是"必须修"。

用法
----
    python scripts/check_clean.py                     # 扫技能根目录
    python scripts/check_clean.py --dir <目录>
    python scripts/check_clean.py --deny zhangsan --deny lisi
        # 额外拉黑字串（比如自己的账号名），适合在分享前临时加上
    python scripts/check_clean.py --quiet             # 只输出结论

退出码：0 = 未发现问题；1 = 有发现；2 = 用法/路径错误。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

# ---- 要扫的文本类型（其余按扩展名跳过，避免二进制误判）----
TEXT_EXT = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml",
            ".cfg", ".ini", ".sh", ".bat", ".ps1", ".html", ".css", ".js"}

# 无扩展名但确实是纯文本的文件。
# ⚠️ 这类文件按扩展名判断会**整份漏掉**——而 `LICENSE` 恰恰是写版权人姓名和
# 联系邮箱的地方，`.gitignore` 里也可能残留本机路径。实测 2026-09-23：
# 加了 LICENSE 之后扫到的文件数还是 7，说明它从来没被扫过。
TEXT_BARE_NAMES = {"license", "licence", "copying", "notice", "authors",
                   "contributors", "readme", "changelog", "makefile",
                   "dockerfile", ".gitignore", ".gitattributes",
                   ".gitmodules", ".editorconfig"}

# 明显是占位/示例的邮箱域名与账号，报出来只会淹没真信号
EMAIL_PLACEHOLDERS = ("example.com", "example.org", "test.com", "test.local",
                      "localhost", "e.com", "email.com")
# 逐字写死的占位邮箱（帮助信息、文档示例里常见）
EMAIL_PLACEHOLDER_EXACT = {"a@b.com", "you@example.com", "me@example.com",
                           "user@example.com", "test@test.com"}
# 代码托管平台的域名：`git@github.com` 这类是 **SSH 地址**，不是某人的邮箱。
# 不排除掉的话，每一份带 SSH 示例的文档都会被报成"发现邮箱"。
GIT_HOST_DOMAINS = ("github.com", "gitee.com", "gitlab.com", "bitbucket.org",
                    "gitcode.com", "codeup.aliyun.com", "gitea.com", "git.sr.ht")
USER_DIR_KEEP = {"public", "default", "default user", "all users", "您", "<用户目录>",
                 "<用户名>", "<user>", "<name>", "用户名", "用户"}

RULES = [
    ("GitHub 令牌", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("Gitee/通用 32 位十六进制令牌", re.compile(r"\b[0-9a-f]{32}\b")),
    ("私钥文件内容", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("Windows 用户目录路径", re.compile(r"[A-Za-z]:\\Users\\([^\\/\s\"'<>|]+)", re.I)),
    ("Unix 家目录路径", re.compile(r"/(?:home|Users)/([A-Za-z0-9._-]+)")),
    ("疑似访问令牌参数", re.compile(r"(?:access_token|api_key|apikey|password|passwd)"
                                    r"\s*[=:]\s*['\"]?[A-Za-z0-9._\-]{8,}", re.I)),
]

# 结构类问题：不是"内容泄漏"，但同样不该带出去
JUNK_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".mypy_cache",
             ".pytest_cache", ".idea", ".vscode"}
JUNK_EXT = {".pyc", ".pyo", ".pyd", ".log", ".tmp", ".bak", ".db", ".sqlite"}


def is_text_file(p: pathlib.Path) -> bool:
    """按扩展名判断，还是按文件名判断——两样都要过，否则会漏掉 LICENSE 这类。"""
    if p.suffix.lower() in TEXT_EXT:
        return True
    return p.name.lower() in TEXT_BARE_NAMES


def scan_text(path: pathlib.Path, extra_deny):
    """返回 [(规则名, 行号, 命中片段, 该行原文)]"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, pat in RULES:
            for m in pat.finditer(line):
                frag = m.group(0)
                # 邮箱占位、SSH 地址、路径白名单：命中但不报
                if name == "邮箱":
                    low = frag.lower()
                    if any(d in low for d in EMAIL_PLACEHOLDERS):
                        continue
                    if low in EMAIL_PLACEHOLDER_EXACT:
                        continue
                    domain = low.rsplit("@", 1)[-1]
                    if any(domain == d or domain.endswith("." + d)
                           for d in GIT_HOST_DOMAINS):
                        continue          # 这是 git 的 SSH / 地址前缀，不是邮箱
                if "家目录路径" in name or "用户目录路径" in name:
                    who = (m.group(1) or "").strip().lower()
                    if who in USER_DIR_KEEP or who.startswith("<"):
                        continue
                hits.append((name, lineno, frag, line.strip()[:120]))
        for word in extra_deny:
            if word and word.lower() in line.lower():
                hits.append((f"自定义拉黑词 {word!r}", lineno,
                             word, line.strip()[:120]))
    return hits


def scan_junk(root: pathlib.Path):
    """返回 [(类别, 相对路径)]"""
    found = []
    for p in root.rglob("*"):
        parts = {s.lower() for s in p.parts}
        if parts & JUNK_DIRS:
            if p.is_dir() and p.name.lower() in JUNK_DIRS:
                found.append(("不该分发的目录", p.relative_to(root)))
            elif p.is_file() and p.suffix.lower() in JUNK_EXT:
                found.append(("不该分发的文件", p.relative_to(root)))
        elif p.is_file() and p.suffix.lower() in JUNK_EXT:
            found.append(("不该分发的文件", p.relative_to(root)))
    return sorted(set(found), key=lambda x: str(x[1]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="分发前自查：扫技能目录里的敏感夹带")
    ap.add_argument("--dir", default=None,
                    help="要扫的目录（默认：本脚本所在技能目录的根）")
    ap.add_argument("--deny", action="append", default=[],
                    help="额外拉黑的字串，可重复，如 --deny 我的账号名")
    ap.add_argument("--quiet", action="store_true", help="只打印结论")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.dir).resolve() if args.dir else \
        pathlib.Path(__file__).resolve().parent.parent
    if not root.is_dir():
        print(f"[错误] 不是目录：{root}", file=sys.stderr)
        return 2

    if not args.quiet:
        print(f"扫描目录：{root}")
        print(f"规则 {len(RULES)} 条 + 结构检查，自定义拉黑词 {args.deny or '无'}\n")

    file_hits, n_files = [], 0
    for p in sorted(root.rglob("*")):
        if not p.is_file() or not is_text_file(p):
            continue
        if any(s.lower() in JUNK_DIRS for s in p.parts):
            continue
        n_files += 1
        for h in scan_text(p, args.deny):
            file_hits.append((p.relative_to(root),) + h)

    junk = scan_junk(root)

    if file_hits and not args.quiet:
        print("=" * 68)
        print(f"内容命中 {len(file_hits)} 处")
        print("=" * 68)
        for rel, rule, lineno, frag, line in file_hits:
            print(f"  {rel}:{lineno}  [{rule}]")
            print(f"      {frag[:80]}")
    if junk and not args.quiet:
        print()
        print("=" * 68)
        print(f"结构问题 {len(junk)} 处（运行/编译会产生，分发前清掉）")
        print("=" * 68)
        for kind, rel in junk:
            print(f"  [{kind}] {rel}")

    total = len(file_hits) + len(junk)
    print()
    print("=" * 68)
    if total == 0:
        print(f"[干净] 扫了 {n_files} 个文本文件，未发现敏感夹带。可以分发。")
        return 0
    print(f"[有发现] {len(file_hits)} 处内容命中 + {len(junk)} 处结构问题。")
    print("逐条看过再决定：有些命中是误报（示例文本、测试桩），")
    print("但**必须有人看过**——这正是这个脚本存在的意义。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
