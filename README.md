# git-sync

**把一个本地文件夹，变成 Gitee / GitHub 上的仓库。**

不用先去网页建空库，不用记 `git remote add`，不用配凭据助手。给脚本四样东西——
账号地址、私人令牌、文件夹路径、仓库名——剩下的事它做完：建远端仓库 → 写
`.gitignore` → 提交 → 推送 → **核对远端 SHA 确认真的推上去了**。

纯 Python 标准库，零第三方依赖，Python 3.8+ 可跑。

---

## 它替代了什么

手工流程（7 步，每步都可能卡住）：

```
1. 浏览器登录 Gitee，找到「新建仓库」，填名字、选私有，创建
2. 复制仓库地址
3. cd 到项目目录，git init
4. git remote add origin <地址>
5. 写 .gitignore（不写就把 venv、模型权重一起传上去）
6. git add / commit
7. git push —— 然后：让我输密码？令牌放哪？凭什么报 403？
```

用 git-sync：

```bash
python git_sync.py            # 问四个问题，然后全自动
```

---

## 安装

技能就是几个纯文本文件，没有安装过程。

**方式一：装成 WorkBuddy 技能**（推荐，装完可以直接对 AI 说"把这个项目传到 Gitee"）

把整个 `git-sync/` 目录拷进用户级技能目录：

```bash
# Windows
xcopy /E /I git-sync "%USERPROFILE%\.workbuddy\skills\git-sync"
# macOS / Linux
cp -r git-sync ~/.workbuddy/skills/git-sync
```

**方式二：当普通脚本用**

把 `scripts/git_sync.py` 拷到任意位置，`python git_sync.py` 直接跑——它不依赖技能
环境，不需要放进技能目录。

**前置要求**

| 要求 | 说明 |
|---|---|
| Python 3.8+ | 只用标准库，不用 `pip install` 任何东西 |
| `git` 在 PATH 里 | 脚本调用系统 git |
| 一个 Gitee 或 GitHub 账号 | 以及该账号的**私人令牌**（不是登录密码，见下） |

---

## 用法一：直接运行（四问向导）

不带参数运行，它自己问：

```
$ python git_sync.py

我能做什么
  把这个文件夹变成一个 Gitee / GitHub 仓库，然后推上去。
  建仓库、写 .gitignore、提交、推送，都我来——你不用先去网页建一个空库。
  推送前我会先体检：有没有疑似密钥、有没有超限的大文件，有问题先说清楚再动手。

我需要你提供 4 样东西
  ① 传到哪个账号  ② 该账号的私人令牌  ③ 要上传的文件夹  ④ 仓库叫什么

三件你可以放心的
  令牌只留在本机、不进仓库、不回显；默认建私有；
  你给的账号地址会和令牌所属账号核对，对不上就停下。

准备（回车 = 采用方括号里的默认值）
① 传到哪个账号？贴主页地址，如 https://gitee.com/你的用户名/: https://gitee.com/zhangsan
② gitee 私人令牌（输入不回显）: ********
③ 要上传哪个文件夹？ [当前目录]: ./myproj
④ 新建的仓库叫什么？ [myproj]:
   建成私有仓库？ [Y/n]:
```

答完就一路跑到底，中途不再打断：

```
三、准备远端仓库
[OK]   gitee → https://gitee.com/zhangsan/myproj（新建）
四、提交并推送
[OK]   远端 origin → https://gitee.com/zhangsan/myproj.git
[OK]   gitee 推送成功（直连）
       已用 git ls-remote 核对远端 refs 与本地 HEAD 一致
```

**第一次运行会多出**上面那段自我介绍；之后就只剩后面两问（令牌、账号已存过会跳过）。
想再看一次介绍：`python git_sync.py --intro`。

---

## 用法二：对 AI 说一句话

装成技能后，不用敲命令。说：

> 把这个项目传到 Gitee，用我的账号

AI 会读 `SKILL.md`，按上面的四问跟你确认，然后带参数跑全自动模式。日常同步
（仓库已配好）更简单：

> 同步一下

---

## 拿令牌

**Gitee**：登录 → 右上角头像 → **设置** → 左侧 **私人令牌** → 生成新令牌
→ **勾选 `projects` 权限**（建仓库要它，最容易漏的一步，漏了建库会 422 失败）
→ 生成后那串**只显示一次**，复制下来。

**GitHub**：Settings → Developer settings → Personal access tokens
→ 经典令牌勾 **`repo`**；细粒度令牌需要 **Administration: 写** + **Contents: 写**。

> ⚠️ 令牌是**账号级凭据**，能建库、能推代码。当密码一样对待，别贴进聊天记录、
> 别写进代码、别提交进仓库。

---

## 命令速查

```bash
# 四问向导（最常用）
python scripts/git_sync.py

# 只体检，不提交不推送 —— 首次同步建议先跑这个
python scripts/git_sync.py --dir ./myproj --account https://gitee.com/zhangsan/ --dry-run

# 全自动（配置好了之后日常用）
python scripts/git_sync.py --dir ./myproj --account https://gitee.com/zhangsan/ --yes

# 一次推两个平台（账号名不同就各给一个 --account）
python scripts/git_sync.py --dir ./myproj --platform both --name myproj \
    --account https://gitee.com/zhangsan/ \
    --account https://github.com/lisi/ --yes

# 建公开仓库（默认私有）
python scripts/git_sync.py --dir ./myproj --account <地址> --yes --public

# 存令牌 / 看配置 / 看自我介绍
python scripts/git_sync.py --set-token gitee=你的令牌
python scripts/git_sync.py --show-config
python scripts/git_sync.py --intro
```

完整参数：`python scripts/git_sync.py --help`

**常用参数**

| 参数 | 作用 |
|---|---|
| `--dir <路径>` | 项目目录，默认当前目录 |
| `--account <地址>` | 目标账号主页地址。**可给多次**（每平台一个） |
| `--name <名称>` | 远端仓库名，默认取目录名 |
| `--platform gitee\|github\|both` | 目标平台 |
| `--private` / `--public` | 仓库可见性（默认私有） |
| `--dry-run` | 只体检出计划，**不碰远端** |
| `--yes` | 绝不询问，全自动 |
| `--proto https\|ssh` | 推送协议，默认 https |
| `--branch <名>` / `--message <说明>` | 分支名 / 提交信息 |

---

## 它怎么保护你

| 担心的事 | 它的做法 |
|---|---|
| 令牌泄漏 | 只写在本机 `~/.workbuddy/git-sync.json`（`chmod 600`）。**不进仓库、不回显、不写进 `.git/config`**——推送用的是带令牌的临时 URL，用完即弃 |
| 传了不该传的 | 推送前逐文件扫**疑似密钥** + **超限大文件**（Gitee 单文件 ≤50MB、GitHub ≤105MB），命中就停下问你 |
| 建错到别人账号下 | 拿你给的账号地址去和令牌所属账号**核对**，对不上立即停止，不闷头建库 |
| 误建公开仓库 | 默认**私有**。要公开必须你明确给 `--public` |
| 静默失败 | **不采信 git 的退出码**——推送后跑 `git ls-remote` 比对远端 SHA 与本地 HEAD，一致才算成功 |
| 可见性没生效 | **不采信建库接口的返回**——建完读回来核对，不一致就用 PATCH 补正（Gitee 的建库接口会静默忽略可见性参数，实测 5 种写法全无效） |
| 擅自改动已有仓库 | 只纠正**本次刚建出来**的仓库。已存在的仓库**绝不改设置**，只报告差异 |
| 脚本卡住不返回 | 全程关闭交互式提示（`GIT_TERMINAL_PROMPT=0`、`GCM_INTERACTIVE=Never`、清空 credential.helper），**永不弹窗、永不等待输入** |
| 网络抖动 | 分级重试：直连 → 清代理 → 绑 IP（仅 GitHub）；**权限类错误立即停**，不盲目重试 |
| 半截操作 | 提交和推送在最后一步才开始，`Ctrl+C` 退出不会留下半截状态 |

---

## 出问题怎么办

| 现象 | 原因 | 对策 |
|---|---|---|
| `Incorrect username or password (access token)` | 令牌无效 / 过期 / 权限不足 | 重新生成令牌，注意勾选权限（见上） |
| 建库返回 422，提示实名认证 | Gitee 要求**账号完成实名认证**才能建库 | 去 Gitee 完成实名认证（无 API 可绕） |
| `远端仓库不存在 / 404` | 仓库名拼错，或令牌无权访问 | 检查 `--name`；确认令牌属于该账号 |
| `账号对不上：你给的地址是 A，但这枚令牌属于 B` | 地址与令牌不是同一个账号 | 换成该账号自己的令牌，或改对 `--account`。**这是防误建的保护** |
| 推送卡住不动 / 弹出凭据选择框 | 系统 git 配了 GUI 凭据助手 | git-sync 已内置规避（清空 credential.helper）；若仍出现，检查自己的 system 级 gitconfig |
| `Connection was reset` / 连接超时 | 链路干扰，**不是配置错误** | 脚本会自动降级重试。`github.com` 的 HTTP/2 有时会被中断，可试 `git -c http.version=HTTP/1.1` |
| GitHub 报「已自动试过清代理 / 绑 IP 仍失败」 | ⚠️ 这句话可能**比实情悲观**：早期版本"绑 IP"只试 1 个地址，那个 IP 一挂就整级作废——**并非所有办法都试过了** | 升级到 v1.9.1（会逐个试 DNS + 候选 IP）。**别急着查代理和令牌**——先换个 IP 试，往往一次就通 |
| 文件太大推不上去 | 超平台配额 | 用 `.gitignore` 排除，或 Git LFS（本工具不支持 LFS） |
| `git status` 显示 `[gone]` | 推送走显式 URL，git 未建 remote-tracking 引用 | 脚本会自动补；手动可 `git fetch <remote>` |
| 想推两个平台但只有一个成功 | 两个平台账号名不同，需各给一个 `--account` | 见上方命令速查 |
| **加了 `--public`，Gitee 上建出来还是私有** | **Gitee 的建库接口会静默忽略可见性参数**（实测 5 种写法全部无效，且接口照返回"成功"） | 本工具建完会**读回来核对**并自动补成公开。若仓库**本来就已存在**，不会擅自改动它——到网页仓库设置里手动改 |
| 仓库已存在，但可见性跟你要的不一样 | 已存在的仓库**故意不自动改**（防止把私库误转公开） | 工具会提示差异，改不改你自己定 |

---

## 能力边界

**做**：`git init`（main 分支）· 生成 `.gitignore` · 调 API 建远端仓库（已存在则复用）·
写本仓库级 `user.name`/`user.email` · 提交 + 推送（HTTPS 令牌 / SSH）· 推送前体检 ·
账号归属核对

**不做**：处理分支合并冲突 · 删仓库或改仓库设置 · 改全局 git 配置 ·
双向同步（**只推不拉**）· Git LFS · 在组织/公司名下建库（只建到个人账号）·
替你完成平台实名认证

---

## 开发：跑测试

```bash
python scripts/test_robustness.py     # 58 项：网络降级、IP 候选、refs 核对、远端复用、删除项
python scripts/test_wizard.py         # 103 项：四问向导、账号解析、按平台核对、可见性核对
python scripts/test_repo_create.py    # 53 项：建库 API 契约、可见性纠正
```

共 **214 项**，全部用桩化（不打真实网络、不碰真实仓库），可以随便跑。
测试里插了一根钉子：`main()` 一旦试图访问真实网络就**直接抛异常**——
避免"漏打一个桩"让测试悄悄降级成假绿。

**分发前自查**（扫令牌、邮箱、手机号、本机路径、`__pycache__`；
按**扩展名 + 文件名**双重判据，所以 `LICENSE` / `.gitignore` 这类无扩展名文件也在扫描范围内）：

```bash
python scripts/check_clean.py
# 退出码 0 = 干净；非 0 = 有东西需要人看一眼
python scripts/check_clean.py --deny 你的账号名     # 额外拉黑自定义字串
```

---

## 目录结构

```
git-sync/
├── SKILL.md                   技能定义（给 AI 读：能力、约定、实测记录）
├── README.md                  本文件（给人读）
├── LICENSE                    MIT 许可证
└── scripts/
    ├── git_sync.py            主程序，纯标准库
    ├── check_clean.py         分发前敏感信息自查
    ├── test_robustness.py     健壮性测试
    ├── test_wizard.py         向导与取值链路测试
    └── test_repo_create.py    建库 API 契约测试
```

---

## 已知限制

- **只在提供令牌的个人账号下建库**，不支持组织/企业命名空间。
- **只推不拉**——它不是同步工具，是"上传"工具。多人协作请用标准 git 工作流。
- `SKILL.md` 的「本机实测硬约束」一节（§2）记录的是**开发机**上的环境事实
  （PortableGit 路径、凭据助手行为、代理端口）。换机器时那些**不是通用规律**，
  以自己机器上 `git --exec-path`、`git config --list` 的实际输出为准。

---

## 许可

**MIT**，全文见 [LICENSE](LICENSE)。

```
Copyright (c) 2026 人间小土鸡
```

你可以自由使用、修改、分发、商用，只需保留版权声明和许可声明。软件按「原样」
（AS IS）提供，不附带任何形式的担保。
