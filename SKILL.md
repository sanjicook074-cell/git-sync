---
name: git-sync
description: 把本地文件夹变成 Gitee / GitHub 上的仓库并推上去 —— 建远端仓库、配远端、提交、推送，全流程非交互、永不挂起。直接运行时是四问向导（问账号 → 问令牌 → 问文件夹 → 问仓库名，然后自动建库推送）；作为技能调用时带 --account/--dir/--yes 全自动，一条命令从「一个本地文件夹」到「代码已在远端」。当用户说「把这个项目传到 Gitee / GitHub」「同步一下代码」「上传到码云」「建个仓库推上去」「初始化仓库并首次推送」「这文件夹还没进版本控制 / 还不是 git 仓库，帮我弄上去」「一键上传代码」「把代码备份到远端」「帮我传上去，用我的账号」，或报告「git push 让我输密码 / 卡住不动 / 弹出选择框」「push 报 Incorrect username or password (access token)」「远端仓库不存在 / 404」「仓库里文件太大推不上去」「推送超限」时使用。**「从本地目录到远端仓库」这一类需求一律用本技能**：只有它会调 API 帮你把远端仓库建出来（无需先去网页手工建库），其余 git 工作流类技能都要求你先手工建好库再给它 URL。
agent_created: true
---

# git-sync —— 本地项目 → Gitee / GitHub

> **对外的一句话介绍（用户问"这是干嘛的"时逐字引这一句，别自己改写）**：
> **「和 AI 聊两句，git 就完事了。」**
>
> 卖点不是"功能更强的 git 工具"，而是**你不用会 git**。仓库简介、README 首句、
> 平台仓库描述三处都用这同一句，别再各写各的。

**脚本**：`scripts/git_sync.py`（纯标准库，Python 3.8+，零依赖）

## 0. 能力边界

| 做 | 不做 |
|---|---|
| **四问向导**：直接运行即可，不必记参数 | 处理分支合并冲突 |
| `git init`（分支 main）+ 生成 .gitignore | 删仓库、改仓库设置 |
| 调 API 建远端仓库（`auto_init=false`，复用已存在的） | 改全局 git 配置 |
| 写本仓库 `user.name` / `user.email`（local，不动 global） | 双向同步（只推不拉） |
| 提交 + 推送（HTTPS 令牌 / SSH） | Git LFS |
| 推送前体检：密钥泄漏 + 大文件超限 | 在组织/公司名下建库（无 `--org`，只建到个人账号） |
| 核对「账号地址」与「令牌所属账号」是否一致 | 替你完成 Gitee 实名认证（只能人工做） |

---

## 1. 两种用法

### 1.0 第一次使用：先自我介绍，再问四件事

**触发条件**：`is_first_run()` 为真——**令牌、账号、提交身份三者全空**（`--show-config` 一眼可看），
或这是他第一次让你上传东西。

⚠️ 反过来说：**第二次之后就看不到这段自我介绍了**，只剩 ③④ 两问自动问。
（实测 2026-09-23：本机三样都存过后重跑，`print_intro()` 不再出现。）
想让它再讲一遍：`python git_sync.py --intro`。用户说"你再说一遍你会干什么"时用这个，
**别让智能体自己复述**——复述就会有出入。

**别一上来就跑参数。** 先把三件事说清楚，再问四项。脚本侧对应 `--intro`（首次运行向导也会自动打印这段）：

> **我能做什么**：把这个文件夹变成一个 Gitee / GitHub 仓库并推上去。建库、写 `.gitignore`、提交、推送都我来——**你不用先去网页建一个空库**。推送前我会先体检密钥和大文件。
>
> **我需要你提供 4 样东西**：
> ① 账号主页地址（如 `https://gitee.com/你的用户名/`）
> ② 私人令牌（不是登录密码。Gitee：设置 → 私人令牌，勾 **projects**；GitHub：Settings → Developer settings → Tokens）
> ③ 要上传的文件夹
> ④ 仓库叫什么
>
> **三件你可以放心**：令牌只留在你本机、不进仓库；默认建私有；你给的账号地址会和令牌所属账号核对，对不上就停下。

问的时候按这个顺序，一项一项来（用户一起答完就一起收）：

| | 问什么 | 收到后怎么用 |
|---|---|---|
| ① | 传到哪个账号？ | 解析出平台 + 用户名，作为 `--account`；地址里带仓库名就拿来当④的默认值 |
| ② | 该账号的私人令牌 | 存进本机配置，**不写进对话回复** |
| ③ | 上传哪个文件夹 | 作为 `--dir`，校验目录存在 |
| ④ | 仓库叫什么 | 作为 `--name`；中文目录名必须换英文名 |

**令牌的两种收法**，优先第一种：
- 让用户自己跑 `python <技能>/scripts/git_sync.py --set-token gitee=令牌`——**令牌不进聊天记录**；
- 用户直接贴出来了，就立刻存进配置并提醒一句"聊天记录里有它，介意的话去平台轮换一次"。

收齐后**先跑 `--dry-run` 把体检结果给他看**，确认了再真推（见 §1.2）。

### 1.1 手动模式：直接运行，四问向导（用户自己跑）

直接 `python git_sync.py`，不问参数，逐步问四项，回车用方括号里的默认值：

| 顺序 | 问什么 | 说明 |
|---|---|---|
| ① | **传到哪个账号？** | 贴 `https://gitee.com/用户名/`、`git@gitee.com:用户名/仓库.git`，或只给用户名（会追问平台） |

> ⚠️ **Gitee 这一问要的是「个人空间地址」，不是昵称，也不是登录名。**
> 个人空间地址（`设置 → 基本设置 → 个人资料 → 个人空间地址`）= 主页 URL 里那一段，
> 也是所有仓库地址的前缀。它**每个账号只能改一次**，改完名下仓库地址全变
> （见 <https://help.gitee.com/account/personal-namespace-settings>）。
> 昵称随时能改、和地址无关。**最省事的做法：让用户打开自己的主页，把地址栏那串贴过来。**
| ② | 该账号的私人令牌 | 输入不回显；已存过就直接跳过不问 |
| ③ | 要上传的文件夹 | 回车 = 当前目录 |
| ④ | 新建的仓库叫什么 | 回车 = 文件夹名（地址里带了仓库名则用它） |
| ⑤ | 建成私有仓库？ | 回车 = 私有 |

问完直接建库 → 提交 → 推送，中间不再打断。已经用命令行给过的项（`--account` / `--dir` / `--name` / `--private`）**不会重复问**。

设计上的两个细节：
- **令牌那一问如果不是必须的就不出现**（已存过），日常重跑只按回车。
- **每个追问都有重试上限**（`MAX_ASK_RETRY=5`）。不会出现"一直回车就无限刷屏"的情况——这是早期版本的真实 bug，已修。

### 1.2 智能体模式：带参数、全自动（我替你跑）

```
python scripts/git_sync.py --dir "<项目目录>" --account https://gitee.com/用户名/ --yes
python scripts/git_sync.py --dir "<项目目录>" --account <地址> --yes --dry-run   # 只体检
python scripts/git_sync.py --dir "<项目目录>" --account <地址> --name 仓库名 --yes
python scripts/git_sync.py --set-token gitee=xxx      # 存令牌
python scripts/git_sync.py --show-config
```

**一次推两个平台**（`--account` 给两次，按平台各配一个）：

```bash
python scripts/git_sync.py --dir "<项目目录>" --platform both --name 仓库名 \
    --account https://gitee.com/A/ --account https://github.com/B/ --yes
```

⚠️ 这条**必须给两个 `--account`**（除非两个平台恰好同名）。
**两个平台的账号名常常不一样**（例如 Gitee `zhangsan` / GitHub `lisi`），
而账号核对是**按平台**做的：少给一个，那个平台会被判成"账号对不上"直接跳过——
或者反过来，脚本会告警"跳过账号归属核对"。两种情况都会说清楚，不会闷着。

- **首次同步某个项目，必须先跑 `--dry-run`**，把体检结果（将推送多少文件、有没有疑似密钥、有没有超限大文件）讲给用户，确认后再跑真推送。
  真推送那一步会**自动调 API 建好远端仓库**，用户不需要提前去网页建空库（见 §2.6）。
  但注意 `--dry-run` 不碰远端，所以它验证不了令牌的建库权限。
- **日常同步可以直接 `--yes`**（仓库已配好、体检无风险时）。
- **脚本绝不裸跑 `git push`**：推送一律绕过凭据助手（`-c credential.helper=` + 带令牌的显式 URL
  + `GIT_CONFIG_SYSTEM=NUL`），保证在**任何机器**上都不会被 GUI 卡住（见 §2）。
  这条约束不因为"本机已经不会弹窗了"而放宽——脚本是要去哪台机器都能跑的。
  手工调试时若在终端跑裸 `git push`，先确认那台机器的助手不会弹窗。
- **绝不把令牌回显到对话里**，报告配置一律用 `--show-config`（自带掩码）。
- 脚本无 TTY 时不会等待输入；缺参数直接报错退出。所以 `--yes` 是智能体路径的默认姿态。
- 缺令牌时**优先让用户自己跑 `--set-token`**（令牌不进聊天记录）；他若直接贴出来了，就用它存进配置，并提醒一句聊天记录有留痕。做法见 §1.0。

### 1.3 一道安全检查：地址账号 vs 令牌账号

用户给的**账号地址**和令牌**实际所属账号**不一致时（例如贴了 A 的主页、却用了 B 的令牌），
早期版本会安静地把仓库建到 B 名下。现在直接拦下并报出两个账号名：

```
[出错] gitee 账号对不上：你给的地址是 someoneelse，但这枚令牌属于 zhangsan
```

忽略大小写；只要建库前先校验令牌就会触发（`--account` 和向导第①问都算给了地址）。
这也意味着：**别把 `--account` 当成装饰性参数**，它真的会被核对。

**核对是按平台做的**（v1.7.0 起）。早先 `expect_login` 是**单值**——于是
`--platform both` 配一个 `--account` 时，两个平台里必然有一个被判成"账号对不上"而跳过，
"一次推两个平台"根本不可能成功（而这恰恰是用户的账号形态：两个平台不同名）。
现在 `expect_logins` 是 `{平台: 账号名}`：给几个地址就核对几个平台，
没给地址的平台会**明确告警"跳过账号归属核对"**，而不是被拦下或静默放过。

### 退出码

| 码 | 含义 | 该做什么 |
|---|---|---|
| 0 | 成功（含「没有新改动」） | — |
| 1 | 参数/环境错误（邮箱缺失、git 缺失、令牌格式） | 补齐参数 |
| 2 | **检出疑似密钥，已中止**（未提交未推送） | 给用户看清单，决定加 .gitignore 还是 `--allow-secrets` |
| 3 | 暂存区空且仓库无任何提交 | 检查 .gitignore 是否排除了全部文件 |
| 4 | 有文件超平台单文件上限 | 加 .gitignore 或改走 Release 附件 |
| 124 | 子进程超时 | 网络问题，检查代理/连通性 |

---

## 2. 本机实测到的硬约束（改脚本前必读）

1. **活跃的 git 是 PortableGit，不是 Program Files 那个。**
   `git --exec-path` → `<用户目录>\.workbuddy\binaries\PortableGit\versions\<版本号>\mingw64\...`
   （实测的原始路径含本机用户名与具体版本号，已隐去；换机器时以 `git --exec-path` 实际输出为准）
   它的 system config 里是 `credential.helper=helper-selector` —— 一个**会弹 GUI 的**凭据助手。
   后果：脚本里裸跑 `git push` 会卡在一个选择框上永不返回。
   对策：脚本全程 `GIT_TERMINAL_PROMPT=0` + `GCM_INTERACTIVE=Never` + `-c credential.helper=`。

2. **令牌不能进 `.git/config`。** 做法是 remote 存干净 URL
   （`https://gitee.com/user/repo.git`），推送时改用「带令牌的显式 URL」且**不带 `-u`**
   （`-u` 会把带令牌的 URL 写进配置）。实测 `.git/config` 里无令牌、无 `@gitee.com`。
   `-c credential.helper=` 同时保证不会顺手把令牌塞进凭据管理器，除非显式加 `--remember-credentials`。

3. **Git Bash 的路径转换会吃掉 `file:///C:/...`。** 测试时用 `file:///` URL 会被转成 `/C:/...`
   并报 "does not appear to be a git repository"，用裸路径或 `MSYS_NO_PATHCONV=1`。

4. **`git check-ignore` 必须在仓库里才能跑**（脱离仓库报 exit 128）。
   所以「哪些文件会被忽略」不能靠猜——脚本的设计是先 `git init` + 写 .gitignore + `git add`，
   **再拿 `git diff --cached --name-only` 的暂存清单去体检**。这样报告就是真正会被推上去的东西：
   被 .gitignore 挡住的 `.env` 不会误报成密钥，没挡住的 `credentials.json` 一定被查出来。

5. **`git reset -q` 必须先于 `git add -A`。** `.gitignore` 对**已暂存**的文件不生效，
   所以每轮都要先清空暂存区再重新 add，否则用户改完 .gitignore 重跑还是会把旧文件带上。

6. **未提交的仓库里 `git rev-parse --abbrev-ref HEAD` 返回 `HEAD` 而不是分支名。**
   要拿真正的分支名（哪怕是 unborn）必须用 `git symbolic-ref --short HEAD`。

7. **配额按十进制 MB 算。** Gitee 个人版：单文件 ≤ 50 MB、单仓库 ≤ 500 MB；
   GitHub：单文件硬限 100 MiB（按 105 MB 保守判）。`human()` 用 1000 进制，和平台公告口径一致。

8. **`core.autocrlf=true` 是系统级设置的。** 含 `.bat`/`.cmd`（尤其 GBK+CRLF 那种）的项目，
   仓库里会被存成 LF。脚本会检测并提示加 `.gitattributes` 写 `*.bat -text`。详见
   `win-py-gui-packaging-release` 技能的 ⑥⑩ 两条。

9. **令牌文件的保护是 Windows 用户目录 ACL，不是 POSIX 权限。**
   `os.chmod(0o600)` 在 NTFS 上只动只读位，`ls` 看到的还是 `-rw-r--r--`。别以为它没生效。

10. **`GIT_CONFIG_SYSTEM=NUL` 比 `-c credential.helper=` 更彻底。**
    helper-selector 卡死的根因是它内部执行 `git config --system -e` 打开编辑器。
    直接跳过 system 级 gitconfig 从根上解决。推送/探测类操作一律带上。

11. **环境里的代理变量会把 git 引向 127.0.0.1，表现为 `schannel: failed to receive handshake`。**
    注意 `http.curloptResolve` 只改 DNS、**绕不开代理**，两者必须同时做。
    但不能默认清代理——国内访问 GitHub 常常正是靠代理才通。所以顺序是**直连优先、失败才清**。

12. **`git push` 的退出码不可信。** 它可能不打印任何输出就退出，也可能在有前次失败残留时
    回一句 `Everything up-to-date`。**唯一可靠判据是 `git ls-remote <url> refs/heads/<branch>`
    的 SHA 与本地 HEAD 是否一致。** 脚本已内置该校验，成功时会明确说"已核对远端 refs"。

13. **API 返回的 `permissions.push:true` 是 owner 视角，不能当作令牌有效的证据。**
    只读令牌也可能显示 true。验证令牌只有一条路：直接用带令牌的 URL 推一次。

14. **`git filter-repo` / `git gc` 在本环境高危**——WorkBuddy 的 safe-delete 会拦截 git 内部
    的删除/重命名，状态机错乱后会把 `.git` 核心文件一起清掉（只剩 `info/` 和 `objects/`）。
    本脚本不含任何 gc / prune / filter-repo 调用；清理历史请改用 `git filter-branch`。

15. **工作区目录里的仓库，git 写 `refs/remotes/**` 和 `refs/tags/**` 会静默失败。**
    实测（2026-09-22，helloai 真项目）：

    | 操作 | 结果 |
    |---|---|
    | `git update-ref refs/heads/probe3 HEAD` | ✅ 写入成功 |
    | `git update-ref refs/tags/probe1 HEAD` | ❌ **退出码 0，文件不存在** |
    | `git tag probe2` | ❌ 退出码 0，tag 不存在 |
    | `git fetch <url> +main:refs/remotes/origin/main` | ❌ 打印 `* [new branch] main -> origin/main`，但引用不存在 |
    | 同样操作在 `%TEMP%` 下的仓库里 | ⚠️ 多数情况正常（`mode='fetch'`），但**不可靠**——实测同一仓库里也会出现写不进去 |
    | 直接用 Python 写 `.git/refs/remotes/<remote>/<branch>` 文件 | ✅ 立即生效，`show-ref` 可见 |

    区别主要在**路径**：工作区内基本必拦、`%TEMP%` 下时好时坏。所以 `git tag` 在本环境的项目里
    也别指望——要打 tag 得有验证手段（`show-ref` 查过才算数）。
    脚本的应对：`ensure_remote_ref()` 先走正常 `git fetch`，再**比对引用内容是否等于本地 HEAD**
    （不是只看引用在不在——过期旧值同样要被当作失败），不行才直接写 ref 文件（见 §2.5）。

    **已排除的怀疑**（2026-09-22 复查，别再重复追）：`core.fscache`（system 配置里是 `true`）。
    加 `-c core.fscache=false` 后曾一次性成功写入 4 个引用，但同条件复测就复现失败，
    关掉 fscache 也修不好。**不是它**。也确认过没有 `.lock` 残留。
    症状的稳定特征是：git 走「建 lock + rename」的写入路径失败，而**直接用 Python/shell
    写 ref 文件（不经过 rename）稳定生效且持久**——所以兜底方案可靠，别去改 git 全局配置。

16. **`helper-selector` 是那类「无端弹出一个窗口把命令卡死」的元凶。**
    system 配置 `PortableGit/versions/<v>/etc/gitconfig` 里写死了：

    ```ini
    [credential]
        helper = helper-selector
    ```

    `git-credential-helper-selector.exe` 是个 **GUI 程序**（让你挑一个凭据助手）。
    一旦 HTTPS 操作缺凭据就调它 → **弹窗、阻塞、直到人工点击**。
    注意 `GIT_TERMINAL_PROMPT=0` **挡不住它**（它不走终端提示那条路），
    所以脚本必须显式禁用助手（§2.1）而不是靠环境变量。

    **关键坑：只在 global 写 `credential.helper=manager` 没用。**
    实测 `git config --get-all credential.helper` 的顺序是 **system 在前、global 在后**：
    `-c credential.helper=manager` → `[helper-selector, manager]`，selector 照样先跑。
    必须用**空值**清空（git 的「清空列表」标记），再接目标助手：

    ```
    git config --global --add credential.helper ""        # 清空点，干掉 system 的 helper-selector
    git config --global --add credential.helper wincred   # Windows 凭据管理器，DPAPI 按用户加密
    ```

    `git-credential-wincred.exe` 在本机 PortableGit 的 `mingw64/bin/` 下有，
    **不弹任何窗口**（GCM `manager` 首次每站点仍会弹自己的登录框，不符合「彻底不弹」）。
    ⚠️ `--get-all` 会同时打印 system 的 `helper-selector`、空值、`wincred` 三行——
    **这是原始配置值，不是最终助手列表**，别被它误导；要看真实结果得用行为验证。

    **验证方法（决定性）**：`GIT_TRACE` 看 git 实际 fork 了谁：

    ```
    printf 'protocol=https\nhost=example.invalid\n\n' \
      | GIT_TERMINAL_PROMPT=0 GIT_TRACE=1 git credential fill 2>&1 | grep credential
    # 只应出现 git-credential-wincred get；出现 helper-selector 就是没改干净
    ```

    另一个快速判据：**若弹窗出现，命令会一直卡住**。所以「带超时跑一次、看是秒返回还是超时」
    就能判定。实测改前 `git ls-remote` 会挂到被杀，改后 3 秒返回。

    ⚠️ **`git credential reject` 会删掉该 host 的「全部」条目，不只当前用户名那条。**
    实测（踩过）：凭据管理器里同时有 `git:https://gitee.com`（陈旧、密码错）和
    `git:https://zhangsan@gitee.com`（正确）两条，执行

    ```
    printf 'protocol=https\nhost=gitee.com\n\n' | git credential reject
    ```

    之后**两条都没了**，Gitee 立刻变成 `could not read Username`。
    要清理陈旧凭据就别图省事——删完必须重新 `git credential approve` 写回正确的那条，
    并用 `git ls-remote <remote>` 实测确认（别只看条目在不在）。
    wincred 的条目名形如 `LegacyGeneric:target=git:https://<用户名>@<host>`，
    可用 `cmdkey /list | grep -a target=git:` 查看（注意 Windows 中文环境下
    `cmdkey` 输出是 GBK，直接 grep 中文字段会乱码，只 grep ASCII 的 `target=` 才稳）。

17. **环境里的代理变量会让 GitHub 时通时不通，而且「API 通、git 不通」。** WorkBuddy 的 shell
    里有 `http_proxy/https_proxy=http://127.0.0.1:1813`（以及 `CODEBUDDY_SERVICE_PROXY_URL`）。
    实测同一时刻三种走法：

    | 走法 | 结果 |
    |---|---|
    | `git ls-remote`（带代理） | ❌ `CONNECT tunnel failed, response 502`，12 秒 |
    | `git ls-remote`（剥掉代理直连） | ❌ 31 秒超时，报 `expected flush after ref listing`（连上了但中途断） |
    | **GitHub REST API**（Python urllib，2 秒） | ✅ **正常返回账号名** |

    也就是说 **REST API 稳定、git 的 HTTPS 传输不稳定**。含义：
    - **建库、查仓库状态、校验令牌** 走 API → 可靠，可以放心依赖；
    - **`git push` / `fetch`** → 可能失败，**失败不代表配置错了**，先重试。
    - 有一次 push 14.9 秒过了，紧接着下一次 fetch 就 502——**别根据单次结果下结论**。
    - 用户自己的终端（PyCharm / cmd）不一定继承这些代理变量，行为可能不同。

18. **Windows 上 `shutil.rmtree(临时目录, ignore_errors=True)` 会静默漏删。**
    git 把 `.git/objects/**` 写成**只读**（`-r--r--r--`），`rmtree` 撞上就
    `PermissionError [WinError 5]`，而 `ignore_errors=True` 把这个异常**吞掉**——
    于是临时目录永远清不掉。实测代价：测试脚本每跑一次就永久留一个目录，
    攒了 **32 个**（本机 C 盘本来就紧）。
    正确写法（`test_wizard.py` / `test_robustness.py` 已内置 `rmtree_force()`）：

    ```python
    def onerror(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)   # 先摘只读位
            func(p)                       # 再重试
        except Exception:
            pass
    shutil.rmtree(path, onerror=onerror)
    ```

    另外 `atexit.register(rmtree_force, tmp)` 覆盖异常 / `sys.exit` 的路径；
    但 **SIGTERM 杀 Windows 进程时 atexit 跑不到**（`timeout` 就是这条路），
    所以再加一道 `purge_stale(prefix, max_age=1800)`：**下次运行时清掉自己留下、
    超过 30 分钟没动过的同前缀目录**（带年龄门槛，避免误删并发运行的另一个测试）。
    凡是造临时目录的脚本都按这套写。

19. **复用 `git remote` 必须按「地址」判，不能按「名字」判。**
    2026-09-22 真实踩到：仓库里 `origin` → Gitee、`mirror` → GitHub，
    单跑 `--platform github` 时旧逻辑只发现「`origin` 的 host 不匹配」，就去挑备选名
    `mirror`——可 `mirror` 已经出现在 `existing` 里，被 `if alt not in used` 挡掉，
    于是落到第三顺位 `github-remote`，**同一个 GitHub 仓库被挂上两个远端名**。
    正确判据是 URL 本身：host 命中 **且** 属主命中 **且** 仓库名精确命中。
    注意必须精确——`helloai` 不能匹配 `helloai-demo`，所以别用 `in` 做子串判断，
    要取 URL 路径最后一段比。实现见 `remote_matches()`，断言在 `test_robustness.py` §7。

20. **「绑 IP」这一级必须用 DNS 当前解析出的地址，写死的老段会帮倒忙。**
    2026-09-23 诊断 `expotool` 推不上去时发现：脚本第三级绑的是写死的
    `140.82.114.4`（美国段），而本机 DNS 解析到 **`20.205.243.166`（亚太段）**。
    绑到老段上等于往一个更远的、多半不通的地址打——这一级不但没兜底，还白等一次超时。
    现在 `github_ips()`：**DNS 的结果排在最前**，写死的候选段只排在它后面。

    ⚠️ 但这条只说对了一半——**后半段见第 23 条**：光"DNS 优先"还不够，
    DNS 给的地址本身也可能不通，那时必须接着试后面的候选段。
    一句话：**顺序**是 DNS 优先，**数量**上不能只给一个。

21. **到 `github.com` 的 HTTP/2 会被中途 RST，切 HTTP/1.1 能立刻恢复。**
    同一分钟内四种组合的实测（2026-09-23，`expotool` 推送失败时）：

    | 组合 | 结果 |
    |---|---|
    | 代理 + DNS IP + HTTP/2 | `CONNECT tunnel failed`（10 秒） |
    | 无代理 + DNS IP + HTTP/2 | `Failed to connect ... after 21057 ms` |
    | 代理 + 美国 IP + HTTP/2 | `Recv failure: Connection was reset` |
    | **代理 + DNS IP + HTTP/1.1** | ✅ **握手成功**（错误变成"要用户名"= 已连通） |
    | 无代理 + DNS IP + HTTP/1.1 | ✅ 同上 |
    | `api.github.com` + HTTP/2 | ✅ 200 / 0.45 秒（一直是好的） |

    同一时刻 API 稳定、git 端点不通，是这类链路干扰的典型指纹。
    **别去改 DNS、别去调代理、别怀疑令牌**——先试 `http.version=HTTP/1.1`。
    脚本已把它做成降级阶梯里的第 2 级（见 §2.5）。注意这**不是**性能优化，平时不要默认开。

    ⚠️ 诚实标注：这次是"HTTP/1.1 通、HTTP/2 不通"的**并列对照**（相隔不到一分钟），
    但紧接着重试时第 1 级 HTTP/2 又通了——说明链路本身在抖。
    所以 HTTP/1.1 是**有效的兜底手段**，但别把它说成"必然的根因修复"。
    另外它治不了"这个 IP 本身不通"——那种情况见第 23 条。

22. **Gitee 的建库接口会静默忽略 `private` 参数——`--public` 必须靠建完 PATCH 兜。**
    实测（2026-09-23，推本技能自身时发现）：请求 `--public`，Gitee 建出来是**私有**，
    而接口返回 **201 成功**，从返回值上**完全看不出异常**。
    用一次性探针仓库把 5 种写法全试了一遍（每个探针建完即删，已确认删净）：

    | 建库时传的 `private` | 建成结果 |
    |---|---|
    | 不带该参数 | 私有 |
    | `False` | 私有 |
    | `"false"` | 私有 |
    | `True` | 私有 |
    | `"true"` | 私有 |
    | **建完再 PATCH `private=false`** | ✅ **公开** |

    → 结论：**该参数在这个接口上根本没被读取**。账号默认私有，请求公开也照样建成私有。
    唯一可靠路径是**建库之后再 PATCH**。PATCH 上还有两个坑（都实测过）：
    - **必须带 `name` 和 `path`**，否则 `400 {'messages': ['name is missing']}`；
    - 不显式传 `private` 就不会改变可见性。

    GitHub 无此问题（JSON body 里的布尔值正常生效），但仍走同一入口，免得两套代码。
    处置：`align_visibility()` —— 建完**读回来核对**，不一致才补一刀；
    且**只纠正刚建出来的仓库**（`is_new=True`）——已存在的仓库按 §0 约定
    **绝不擅自改**（把别人的私库悄悄转公开，比"没改成"严重得多），只报告差异。
    代码里 `repo_visibility()` 读不到时返回 `None` 而不是 `False`——
    "没读出来"和"读出来是公开"是两件事，混淆会直接导致误判。

    ⚠️ 这条的通用教训：**"接口返回成功"不等于"你想要的结果发生了"。**
    和 §2.5 里"git 退出码不可信"是同一类错误。凡是**设参数**的操作，
    都要**读回来验证**——尤其是这个参数决定了仓库是公开还是私有。

23. **GitHub 的"绑 IP"必须给多个候选——只试一个 IP 等于只有一次机会。**
    实测（2026-09-23 03:0x，推本技能到 GitHub 时）：本机 DNS 解析出
    `20.205.243.166`，当时**挂住不通**（连接无响应，白等满 21 秒超时）；
    而**同一条链路**里 `140.82.121.4` **稳定可通（连续 3/3 成功）**，
    其余候选（`140.82.112.4` / `113.4` / `116.4`、`20.201.28.151`）当时不通。

    旧代码是 `github_ips()` 里 `return dns_ips[:1]`，调用处也只取 1 个 →
    "绑 IP"这一级**退化成只有一次机会**：DNS 给的地址一挂，整级就废，
    脚本报「已自动试过清代理 / 绑 IP 仍失败」。⚠️ **这句话比实情悲观**：
    结论没错（确实没推上去），但归因是错的——**会让人去查代理、查令牌，
    而不是"换个 IP 再试"**。这是"报错信息误导排查方向"的典型。

    现在：`github_ips(limit=4)` 返回 **DNS 结果 + 候选段**（去重、限量），逐个试；
    `PUSH_TOTAL_BUDGET` 从 300 提到 420 秒，给"每个不通的 IP 各白等一次约 21 秒"留预算。

    ⚠️ **IP 会轮换，候选段不是"正确答案"**，只是候选。现场找活的用这个：

    ```bash
    # 先清代理——本机代理会把 CONNECT 拦掉，混在结果里干扰判断
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
    for ip in 140.82.121.4 140.82.112.4 140.82.113.4 140.82.114.4 20.205.243.166; do
      printf "%-16s " "$ip"
      out=$(timeout 12 git -c credential.helper= -c http.version=HTTP/1.1 \
            -c http.curloptResolve=github.com:443:$ip ls-remote <仓库URL> 2>&1 | head -1)
      [ -z "$out" ] && echo "（挂住/超时）" || echo "${out:0:60}"
    done
    ```

    **怎么读结果**（比跑脚本更容易看错，逐条对应实测）：
    | 输出 | 判定 |
    |---|---|
    | `SHA  refs/heads/...` | ✅ **通**，用它 |
    | `Empty reply from server` / `Recv failure: Connection was reset` | 连上了但被掐断，不算通 |
    | **完全没有输出**（被 `timeout` 杀掉） | 挂住——最坏的一种，白等满超时 |
    | `CONNECT tunnel failed` | 是**代理**在拦，不是这个 IP 的问题，先清代理 |
    | `fatal: expected flush after ref listing` | ⚠️ **不能判为不通**！这是"收到一半"，重试往往就通了（实测 3/3 成功） |

    最后一行是个反例教训：**同一条错误信息，可能只是"这一次"。**
    先重试 2–3 次再下结论，否则会把好 IP 划掉。

---

## 2.5 推送健壮性：分级降级 + 真实结果校验

网络这层不是"配好就完事"，脚本内置分级重试（v1.7.0 起是 4 级 + GitHub 专有第 5 级）：

| 级别 | 策略 | 何时进入下一级 |
|---|---|---|
| 1 | **直连**（保留用户代理设置，HTTP/2） | 失败且判定为网络类错误 |
| 2 | **直连 + HTTP/1.1**（`http.version=HTTP/1.1`） | 同上 |
| 3 | **清代理**（删大小写共 6 个代理变量，HTTP/2） | 同上 |
| 4 | **清代理 + HTTP/1.1** | 同上 |
| 5 | **清代理 + 绑 DNS 当前 IP + HTTP/1.1**（仅 GitHub，`http.curloptResolve`） | — |

**为什么第 2 级是 HTTP/1.1 而不是继续折腾代理**：2026-09-23 实测，本机到
`github.com:443` 的 **HTTP/2 连接被中途 RST**（`Recv failure: Connection was reset` /
`Failed to connect ... after 21057 ms`），而**同一分钟内**把 `http.version` 改成 HTTP/1.1，
同一个地址立刻能完成 TLS 握手（报的错从"连不上"变成"要用户名"——说明链路已经通了）。
对照组里 `api.github.com` 走 HTTP/2 全程正常（0.45 秒 200）。
所以这是**链路对 HTTP/2 的干扰**，不是 DNS、不是代理、不是令牌。
注意级别顺序有讲究：**HTTP/1.1 必须排在直连之后**，否则所有正常推送都被降级拖慢。

- **权限类错误立即停下，不盲目重试**（实测：坏令牌连 Gitee 报
  `Incorrect username or password` 时 1.6 秒就停）。Gitee / Codeup / 自建 GitLab
  **不做 IP 轮换**——它们没有权威固定 IP 段，硬绑反而出错。
- ⚠️ **这条规矩有一个例外，是踩出来的（2026-09-23，v1.11.0）**：
  **代理在拒绝 CONNECT 时，git 会借"认证失败"来报**——它把 URL 里带的凭据拿去当
  **代理**凭据试一遍，被拒后打印 `fatal: Authentication failed for 'https://...'`。
  这句话落进"权限类错误"分类里，**阶梯就在第 1 级断死**，永远走不到"清代理"那一级。
  实测现场：本地代理回 `CONNECT tunnel failed, response 502`，脚本报
  `Authentication failed`；而**同一枚令牌几分钟前刚成功 PATCH 过 api.github.com**。
  → 规则：**认证类错误 + 环境里存在代理变量 → 不停，继续升到"清代理"各档**；
  没配代理还报认证失败，那才是真的凭据问题，立即停。
  最终结论仍如实报失败，只是错误文本后面补一句"这条可能是代理冒充的，先查代理别急着换令牌"。
  → 通用教训：**"看起来像权限问题"和"是权限问题"是两件事。**
  分类函数把两者混同，就会让**整个降级机制在最需要它的场景里失效**——
  而且失败报告会长得非常像"就是你的令牌不对"。
- **绑 IP 要用 DNS 当前结果，不是写死的老段**（见 §2 第 20 条）。
- 单次推送上限 150 秒，整条链总预算 300 秒，不会无限拖。
- 每次尝试后都跑 `git ls-remote` 核对 SHA，所以**静默成功和静默失败都能正确判定**。
- 推送成功后补 `refs/remotes/<remote>/<branch>`（走 `ensure_remote_ref()`）：推送用的是
  「带令牌的显式 URL」而非 remote 名，git 不会自己建这个引用；缺了它用户随后
  `git status` 会显示 `[gone]`、IDE 也看不到远端状态。正常工作流先 `git fetch` 并**验证
  引用落地**，落不了地才直接写 ref 文件（原因见 §2 第 15 条）。
- 回显 git/远端输出前清掉 ANSI 颜色码（Gitee 的 `remote:` 报错自带颜色）。

**第一级就成功时不会有任何额外成本**——`expotool` 首次推送 13.7 秒走完（Gitee 10 秒级、
GitHub 十几秒级）。分级只在真失败时才展开。

---

## 2.6 远程建库：不需要先去网页建空仓库

这是本技能与其它 git 工作流技能的分水岭。流程第「三」步直接调平台 API 把仓库建出来，
**你不用先打开 gitee.com / github.com 手工建库、也不用复制粘贴 URL**。

| 平台 | 建库接口 | 认证方式 | 请求要点 |
|---|---|---|---|
| Gitee | `POST https://gitee.com/api/v5/user/repos` | 令牌走 **form 字段** `access_token` | 必须同时给 `name` 和 `path`（同名） |
| GitHub | `POST https://api.github.com/user/repos` | 令牌走 **Header** `Authorization: Bearer` | 走 JSON body，无 `path` 字段 |

三条关键设计：

1. **`auto_init=false` 是刻意的。** 若让平台用 README 初始化仓库，远端就有了一个
   本地没有的提交，首次 `git push` 会被判成非快进（non-fast-forward）直接拒收。
   建"真正空"的仓库才能一次推上去。
2. **已存在同名仓库按成功处理**，不报错——Gitee 回 `已存在同地址仓库(忽略大小写)`，
   GitHub 回 `422 name already exists`。两种情况都复用它继续推。这是可重复运行的前提。
3. **建库前先校验令牌**（`GET /api/v5/user` 或 `/user`）拿到账号名并记进配置，
   避免"令牌是别人的、库建到别人账号下"。

### 前提：Gitee 必须完成实名认证

**未实名认证的账号调建库接口会被拒绝**（无 API 可绕）：

```
422 {"error":{"base":["当前账户尚未认证身份，请通过「个人设置 - 帐号信息」下完成身份认证后再操作"]}}
```

处置：去 Gitee「个人设置 → 帐号信息」完成身份认证，再重跑同一条命令即可（脚本会打印这段提示）。
注意这个 422 **不能**被当成"仓库已存在"——脚本按错误正文判断，此条会正确判为失败。

### 已验证到什么程度（诚实标注）

| 项 | 状态 |
|---|---|
| 请求 URL / 方法 / 请求体字段 / 认证位置 | ✅ 离线测试 31 项断言全过（`test_repo_create.py`） |
| 两个建库端点真实存在（不是拼错的网址） | ✅ 用**坏令牌**实打实联网打了一次：Gitee 回 `401 Access token does not exist`、GitHub 回 `401 Bad credentials`——是鉴权错误而非 404，说明路径正确 |
| 三种结局判定（新建 / 已存在复用 / 失败） | ✅ 含 Gitee 嵌套错误体 `{"error":{"base":[...]}}` 的展开 |
| 取值真的流到建库与推送（向导值 → 建库调用 → 推送调用） | ✅ 桩化跑完整 `main()`，18 项断言（`test_wizard.py` §10） |
| **成功建出一个真仓库** | ✅ **已端到端跑通**（2026-09-22）：真令牌建出私有仓库、推送成功、API 复核 `full_name` 与 `private` 均正确。详见 §7.5 |

⚠️ `--dry-run` 会在建库之前就返回，**不会碰远端**，所以它无法判断你的令牌有没有"建库权限"。
令牌的问题要等真跑才暴露——这是已知的行为取舍，不是 bug。

---

## 3. 一次性准备

```bash
# 1) 身份（也可用 --user-name / --user-email，会一并存进配置）
python scripts/git_sync.py --user-name "你的名字" --user-email "you@example.com"

# 2) Gitee 私人令牌：设置 → 私人令牌，勾选 projects 权限
#    注意 2021-12 起 Gitee 全面停用账号密码，只能令牌
python scripts/git_sync.py --set-token gitee=你的令牌
# 存的同时会调 /api/v5/user 校验并记下账号名

# 3) GitHub 令牌：Fine-grained PAT，勾 repo 权限
python scripts/git_sync.py --set-token github=ghp_xxx
```

- 令牌也可走环境变量 `GITEE_TOKEN` / `GITHUB_TOKEN`（优先级低于配置文件）。
- 配置文件：`~/.workbuddy/git-sync.json`。
- SSH 模式（`--proto ssh`）需要先有 `~/.ssh/id_ed25519`——**当前该目录不存在**，用 HTTPS 即可。

## 4. 常用参数

| 参数 | 说明 |
|---|---|
| `--account <地址\|用户名>` | 目标账号。支持 `https://gitee.com/用户名/`、`git@gitee.com:用户名/仓库.git`、`用户名`（会追问平台）。**会与令牌所属账号核对**（§1.3）。**可以给多次**，每个平台一个（见下） |
| `--dir <目录>` | 项目目录。不给则在向导里问；非交互时退回当前目录 |
| `--platform gitee\|github\|both` | 默认读配置（gitee）。`both` 会建两个远端：`origin` + `mirror` |
| `--private` / `--public` | 默认私有 |
| `--name` | 远端仓库名。目录名是中文时必须显式指定英文名 |
| `--yes` | 全自动，**跳面向导**（智能体路径要用它） |
| `--allow-secrets` | 确认密钥无误后强行继续 |
| `--allow-large` | 超限文件也试着推（远端大概率拒收） |
| `--no-gitignore` | 不生成 .gitignore |
| `--remember-credentials` | 推成功后把令牌交给凭据管理器（**可能弹窗，谨慎**） |

**双平台为什么用两个 remote 而不是一个 origin 挂两个 push URL**：HTTPS 下两个平台要两套令牌，
一个 remote 只能配一套凭据。所以 `origin` + `mirror` 各自独立认证，由脚本一次跑完。

**远端怎么复用（v1.6.0 起）**：**先按「地址」认，远端名只是备选**。
脚本扫描所有已有远端，谁的 URL 同时满足「host 对 + 属主对 + 仓库名精确对」就复用它——
所以哪怕你的 GitHub 远端当初落在 `mirror`、之后又手动改了别的名字，也不会重复添加。

- 找不到可复用的，才按 `origin` / `mirror` 依次占位；
- 名字都被占用（例如 `origin` 指向了别的仓库）才退到备选名 / `<平台>-remote`；
- **绝不覆盖已有远端**。

⚠️ 这条是踩出来的（2026-09-22）：旧逻辑只按**名字**判断，`origin` 给了 Gitee 之后
单跑 `--platform github`，明明 `mirror` 已经指向那个 GitHub 仓库，它却视而不见、
又加了个 `github-remote`——同一个仓库挂两个远端名，`git remote -v` 一片混乱。
现已改为按地址复用（`remote_matches()` / `resolve_remotes()`，`test_robustness.py` §7 有 17 条断言）。
若手上还有旧版本留下的重复远端，`git remote remove <多余的那个>` 删掉即可。

⚠️ **`--name` 是全局的，两个平台共用同一个仓库名**。想两边叫不一样（例：Gitee `helloai-demo`、
GitHub `helloai`）不能一次跑完——必须分两次、各带各的 `--name`。分次跑时**别用 `--platform both`**，
否则会给另一个平台建出一个同名的新仓库（真实副作用，不是提示）。

---

## 5. 故障 → 处置

| 症状 | 原因 | 处置 |
|---|---|---|
| `Incorrect username or password (access token)` | 用密码而非令牌；或令牌没勾 projects | `--set-token gitee=` 重设 |
| `HTTP 404` / `not found` 推送失败 | 令牌与仓库账号不匹配；GitHub 对私有仓库也回 `Repository not found`（刻意隐藏） | `--show-config` 核对账号与令牌 |
| 建库/推送打到别人名下或 404 | 把 Gitee **昵称**当成了账号地址 | 用「个人空间地址」（主页 URL 那段），见 §1.1 的提示 |
| 建库报 `已存在同地址仓库(忽略大小写)` | 远端已有同名仓库 | 脚本已自动复用，不报错 |
| 建库报 `422 当前账户尚未认证身份` | **Gitee 实名认证门槛**，无 API 可绕 | 去「个人设置 → 帐号信息」完成实名认证后重跑 |
| 建库报 `401 Access token does not exist` / `Bad credentials` | 令牌无效或已删 | 重新生成令牌，注意只显示一次 |
| 建库报 `403 您的账户已被限制创建仓库` | 账号被平台限制 | 联系平台申诉；临时可先在网页建好库再让脚本推送 |
| 报 `gitee 账号对不上：你给的地址是 A，但这枚令牌属于 B` | 地址与令牌不是同一个账号 | 换成该账号自己的令牌，或把 `--account` 改成正确地址。**这是防误建到别人账号下的保护** |
| `--platform both` 时某个平台被跳过 / 报"账号对不上"但它其实没错 | v1.6.0 及以前账号核对是**单值**的，两个平台不同名时必然误判一个 | 升级到 v1.7.0（按平台核对），并**每个平台各给一个 `--account`**：`--account https://gitee.com/A/ --account https://github.com/B/` |
| GitHub 报 `Recv failure: Connection was reset` / `Failed to connect ... after 21057 ms`，但 `api.github.com` 秒回 | 链路对 **HTTP/2** 的干扰（不是 DNS、不是代理、不是令牌） | 加 `-c http.version=HTTP/1.1` 试一次（脚本 v1.7.0 已做成第 2 级自动兜底）。见 §2 第 21 条 |
| 绑了 IP 反而更慢/更不通 | 绑的是写死的老 IP 段，与本机 DNS 解析出的段不同 | v1.7.0 起 `github_ips()` 把 DNS 结果排在最前；候选段只排在其后（§2 第 20 条） |
| GitHub 报**「已自动试过清代理 / 绑 IP 仍失败」** | ⚠️ 这句话**比实情悲观**：v1.9.0 及以前"绑 IP"只试 **1 个**地址，DNS 给的 IP 一挂，整级就废——**并不是所有办法都试过了** | 升级到 v1.9.1（会逐个试 DNS + 候选段）。或按 §2 第 23 条现场扫一个能通的 IP。**别急着去查代理和令牌** |
| 报 `fatal: Authentication failed for 'https://...'`，但令牌明明是对的（刚用它调过 API） | ⚠️ **可能是代理冒充的**：代理拒绝 CONNECT（407/502）时，git 会把 URL 里带的凭据当**代理**凭据试，被拒后借"认证失败"这句话报出来。v1.10.1 及以前，它会被当成权限问题 → **阶梯第 1 级就断死** | 升级到 v1.11.0（检测到代理时不再一级断死）。自诊：直连探一次看是不是 `CONNECT tunnel failed, response 502`；临时 `unset https_proxy` 再推。**先查代理，别急着换令牌** |
| **`--public` 了，Gitee 上建出来还是私有** | **Gitee 建库接口静默忽略 `private`**（5 种写法实测全无效，且返回 201） | v1.9.0 起建完自动读回来核对并 PATCH 补正（§2 第 22 条）。若仓库**本来就已存在**，脚本按约定不改它——到网页设置里手动改 |
| 仓库已存在，但可见性跟你要的不一样 | 已存在的仓库**故意不自动改**（防把私库误转公开） | 脚本会报告差异，改不改由你定：到网页仓库设置里改 |
| 向导里一直问同一句、停不下来 | 早期版本的死循环 bug（已修）。现在连续 5 次拿不到有效值就退出并给出替代命令 | 直接给参数：`--account` / `--dir` / `--name` |
| 把账号地址填进了「令牌」那一问 | 常见手误 | 会被当场指出并要求重填（令牌不含 `://`） |
| 想要"某个组织/公司下"的仓库 | 脚本只在**个人账号**下建库（没有 `--org`） | 先在网页建到组织下，脚本会自动复用同名仓库 |
| 推送成功但远端没有 README | 正常。仓库是空初始化的（`auto_init=false`），要靠本地推上去 | 无需处理 |
| `! [remote rejected] ... without 'workflow' scope` | **不是 403**。fine-grained PAT 改 `.github/workflows/` 下文件需单独的 Workflows 权限 | 给令牌补 Workflows: Read and write；急的话先把 workflow 文件临时移出 `workflows/` 目录再推 |
| `403 Permission to ... denied` | 两种可能：令牌本身无写权限；或凭据串号 | 用带令牌的 URL 直推一次区分：仍 403 = 令牌没权限；成功 = 之前用的是错凭据 |
| `schannel: failed to receive handshake` | 代理变量把 git 引向 127.0.0.1 | 脚本第 2 级会自动清代理重试；若必须走代理，请自行配好 `http.proxy` |
| push 卡住无输出、永不返回 | helper-selector 内部跑 `git config --system -e` 打开编辑器 | 脚本已用 `GIT_CONFIG_SYSTEM=NUL` 从根上绕过；手工命令请照抄 |
| push 回 `Everything up-to-date` 但其实没推上去 | 前次失败的残留输出，退出码不可信 | 脚本已用 `git ls-remote` 核对 SHA，以脚本结论为准 |
| 单文件超限 | Gitee 50 MB / GitHub 105 MB | 加 .gitignore，大文件改走 Release 附件 |
| 想让人下载仓库里的文件却 403 | Gitee 公开仓库原始数据 > 10 MB 必须登录 | 走 Release 附件（匿名可下） |
| 推送成功但 `git status` 显示 `[gone]` | remote-tracking 引用没建；本环境工作区里 git 写 `refs/remotes/**` 会静默失败（§2 第 15 条） | 重跑一次脚本（已内置兜底），或直接写 `.git/refs/remotes/<remote>/<branch>` 文件 |
| `git ls-remote` / `git fetch` 报 `could not read Username for 'https://gitee.com'` | remote 是裸 HTTPS，而本机凭据助手被禁用，git 自己拿不到令牌 | 属预期现象。用本脚本（自带令牌）；或 `--remember-credentials` 把令牌交给凭据管理器 |
| `git tag` 静默不生效 | 同上，写 `refs/tags/**` 也被拦 | 打完用 `git show-ref` 确认；本环境别依赖 tag |
| `git remote -v` 里两个远端指向同一仓库（如 `mirror` + `github-remote`） | v1.5.2 及以前按**名字**挑远端（§2 第 19 条） | 删掉多余的那个：`git remote remove github-remote`；v1.6.0 起已按地址复用，不会再发生 |
| 体检说「将推送 1 项改动，合计 0 B」 | 这一项是**删除**，体积本来就是 0 | v1.6.0 起会单独写明「其中 N 项是删除（…）」；老版本看 `git diff --cached --name-status` 即可 |
| 提交时弹 .NET Framework 4.7.2 安装引导 | GCM 依赖 .NET 4.7.2+ | 改用 git 自带的 `wincred`：`git config --local credential.helper wincred` |
| `~/.git-credentials` 里出现明文令牌 | 全局 `credential.helper=store` | 只在本仓库设 helper，别用 global store |
| PyCharm 点 push 被弹窗拦住 | 凭据助手是需要 GUI 交互的那种（本机原为 `helper-selector`） | **本机已于 2026-09-22 根治**：global 加「空值 + `wincred`」覆盖，见 §2 第 16 条。其他机器按同样办法处理 |
| 默认分支显示成 master | 空仓库的平台默认分支 | 到仓库设置里把默认分支改成 main |
| `.git` 目录残缺（只剩 `info/` 和 `objects/`） | 跑了 `git gc` / `git filter-repo`，safe-delete 拦截内部删除导致状态机错乱 | 动手前先 `cp -r .git` 备份；改用 `git filter-branch`；**永远别在本环境跑 git gc** |

## 6. 接缝（以及为什么不需要另外装技能）

2026-09-22 把推荐市场里三个沾边的技能拉下来逐个读过实现，结论：

> **2026-09-23 更新：这四个技能已全部卸载**（`github-workflow` / `git-workflow-and-versioning` /
> `code-review-report` / `github-workflow__skillhub`）。本技能稳定跑通四轮真实演练后，
> 它们不再提供增量价值，只留下"抢路由"的副作用。下表保留为**「当初为什么判定它们不能替代」
> 的论证记录**，不再描述当前的安装状态——现在本机「本地目录 → 远端仓库」只有本技能一条路。


| 技能 | 实现形态 | 为什么不能替代本技能 |
|---|---|---|
| `github-workflow` v2.0.0 | 16 个 bash 脚本共 4616 行，含 `init_push.sh`/`check_auth.sh`/`net_check.sh`、9 套 gitignore 模板、7 份参考文档 | **明确声明「不适用：从零创建远程仓库」**——它要求你先手工在网页上把仓库建好再给它 URL。而且路径 A 要走 6 个阶段逐步确认。它擅长的分支规范/PR/发版/worktree 不是"把代码传上去"这一步 |
| `git-workflow-and-versioning` v1.0.0 | **纯方法论文档，零脚本** | 讲原子提交、SemVer、changelog 的规范，完全不碰推送，没有执行能力 |
| `code-review-report` v2.0.4 | 一个规则式 diff 检查器（4 条正则），需你先 `git diff > change.diff` 再喂给它 | 只有 SEC001（硬编码密码）与本技能的密钥体检重叠，且本技能范围更宽（文件名规则 + 6 类内容规则 + 扫暂存区）。它不推送任何东西 |
| `github-workflow__skillhub`（本机已装） | 只有一份 SKILL.md，靠外部插件 `openclaw-morphixai` + `MORPHIXAI_API_KEY` + 在 morphix.app 关联账号 | **典型的"还要管一堆别的东西"**，且它依赖的 `mx_github` 工具在本环境不存在，属不可用状态 |

**本技能是这四个里唯一能"从零到推送完成"一步闭环的**——因为只有它会**调 API 建远端仓库**
（`auto_init=false`，无需你先去网页操作）。上面那个 v2.0.0 的技术长处已经按需吸收进来
（§2 第 10~14 条、§2.5 的三级降级与 refs 校验）。

⚠️ **路由冲突（2026-09-22 复查发现 → 2026-09-23 已解除）**：`github-workflow` 的 **description 和正文自相矛盾**——
它的 description 写着「当用户需要**初始化 Git 仓库并首次推送**」，正文第 27 行却写着
「**不适用**场景：从零创建远程仓库」。技能是靠 description 被选中的，所以只说
"把这个项目传到 GitHub"，有被路由到它、然后被要求先手工建库的风险。
应对：本技能的 description 已显式收回「初始化仓库并首次推送」「还没进版本控制」这类说法，
并声明"从本地目录到远端仓库一律用本技能"。**2026-09-23 起 `github-workflow` 已被卸载，
这条冲突随之消失**；若以后又出现选错技能的情况，先看是不是它（或同类技能）
被重新安装、又被安装了更新版本覆盖了描述。

其他接缝：

- **大文件 / 安装包分发** → `win-py-gui-packaging-release`。仓库只放源码，成品走 Release 附件；
  Gitee 个人版单仓库 500 MB、单文件 50 MB 是硬约束。
- 同步完成后如需对外发链接，用 `present_files` 或站点发布技能。

---

## 7. 实测记录（2026-09-22）

### 7.1 流程用例（隔离 USERPROFILE，5 组全过）

| 用例 | 结果 |
|---|---|
| 含 47 MB `outputs/` + `.env` + `credentials.json` 的项目 | 报告「将推送 5 个文件，合计 678 B」——`outputs/`、`data/`、`.env` 被 .gitignore 挡住后**不再计入体积、不再误报密钥**；`credentials.json` 正确拦下，退出码 2 |
| 放下 55 MB 未被忽略的文件 | 触发超限闸门，退出码 4，并给出「改 .gitignore 或走 Release」的处置 |
| 完整提交（无令牌） | 正确提交并显示「没有可用平台，只在本地完成了提交」 |
| 干净仓库重复运行 | 跳过提交、继续检查远端，退出码 0（**不再误报「暂存区为空」**） |
| 坏令牌推不存在的仓库 | 25 秒快速失败、**无任何交互式等待**（反挂起保证） |

推送机制本身用本地裸仓库代替远端验证通过：显式 URL + 禁用凭据助手推送成功建分支，
之后手工设好 `branch.main.remote/merge` 裸 `git push` 也能用，且 `.git/config` 里**没有令牌**。

### 7.2 健壮性用例（`python scripts/test_robustness.py`，56 项全过）

| 组 | 覆盖 |
|---|---|
| 环境构造 | `GIT_CONFIG_SYSTEM=NUL` 生效、strip 模式能真正删掉代理变量、`git_prefix` 的 IP 参数拼装正确 |
| 错误分类 | 认得 `schannel` / `server closed abruptly`；**不误判** 403 权限错误与 `remote rejected`(workflow) |
| refs 校验 | 空仓库读到空字符串不报错；推送后远端 SHA == 本地 HEAD |
| 全流程 | `push_platform` 报告通道名、不误报静默成功、远端确实收到提交、`-u` 设好 upstream |
| 不盲目重试 | 坏令牌连真实 gitee.com 报 `Incorrect username or password` → **1.6 秒停下**，不升级到清代理/IP 级别 |
| 远端复用（§7） | `remote_matches` 10 种地址写法（https / 无 `.git` / SSH 简写 / 带账号前缀 / 尾斜杠 / 大小写 / 平台错 / 仓库名前缀陷阱 / 别人账号同名仓库）；再在真仓库里断言「单跑 github 复用 mirror、不新增第三个远端」和「双平台各复用 origin/mirror」 |
| 删除项（§8） | 真仓库删文件 → 暂存 → `staged_deletions()` 认出 `['b.txt']`、体检报告带 `deleted` 字段、删除项体积为 0 |
| 网络兜底（§9） | `github_ips()` 非空 / ≤2 个 / 可重复调用稳定 / DNS 可用时首个即 DNS 地址且不掺写死老段；桩化 `push_platform` 断言"网络类错误会一路升级到绑 IP、绑的就是 DNS 那个、HTTP/1.1 只出现在第 2 级之后、第一级不带 HTTP/1.1、全失败就老实返回 False" |

### 7.3 远程建库用例（`python scripts/test_repo_create.py`，53 项全过）

用假的 HTTP 层替换 `http_json`，把"到底发出去了什么"抓出来断言，不联网、不耗令牌。

| 组 | 覆盖 |
|---|---|
| Gitee 建库请求 | `POST /api/v5/user/repos`、令牌在 form、`name`+`path` 同时给、`auto_init=false`、编码后形态正确 |
| Gitee 边界 | `已存在同地址仓库` → 复用不报错；**422 实名认证报错 → 判为失败且不被误认成"已存在"** |
| GitHub 建库请求 | `POST api.github.com/user/repos`、令牌在 Header、走 JSON body、无 `path` 字段 |
| GitHub 边界 | `422 name already exists` → 复用；`401 Bad credentials` → 失败 |
| 错误翻译 | 权限/401/403/实名 各归各类；**无法归类时返回空、不瞎猜** |

联网部分（用**坏令牌**探端点，不产生任何副作用）：

```
Gitee  POST /api/v5/user/repos   → 401 401 Unauthorized: Access token does not exist
GitHub POST api.github.com/user/repos → 401 Bad credentials
```

两者都是鉴权错误、**不是 404**，证明端点地址正确且可达。

### 7.4 向导与取值链路（`python scripts/test_wizard.py`，103 项全过）

用脚本化的回答驱动向导，把"到底问了什么、取到了什么、有没有流到下游"抓出来断言。
每次用例用**独立的临时配置文件**，避免前一个用例存的令牌污染后一个。

**要复现「全新用户第一眼看到她什么」**（没令牌、没账号、没提交身份），把
`USERPROFILE` / `HOME` 指到一个空目录再跑就行——`CONFIG_PATH` 是从 `Path.home()` 推出来的，
于是 `is_first_run()` 真的返回真，会完整打印 `print_intro()` 那一段。
再把交互层打开并喂进脚本化答案（`G.INTERACTIVE = True` + 替换 `input` / `getpass.getpass`），
就能原样重放「自我介绍 → 四问 → 体检」整段，用来回归文案与提问顺序。
注意这只换了输入来源，业务逻辑一行没动。（2026-09-23 实测：`--dry-run` 下整段 40 行输出可复现。）

| 组 | 覆盖 |
|---|---|
| `parse_account` | 16 种写法：带/不带协议、`www.`、SSH 形式、`git@host:用户名.git`、地址里带仓库名、大小写、前后空格；4 种非法输入被拒 |
| 四问取值 | 平台/账号/目录/仓库名/私有 全部正确；地址里带了仓库名时用它当默认 |
| 非法输入重问 | 坏地址 → 重问①；坏目录 → 重问③；空仓库名 → 回落到默认值 |
| 只给用户名 | **显式追问平台**（不会默默替你选）；平台答错则退回第①问 |
| 令牌 | 把账号地址错填进令牌那一问会被当场挡下；空令牌被挡；已存过则不再问 |
| 已给参数不重复问 | `--account` / `--dir` / `--name` / `--private` 给过的都不再问 |
| 集成（桩化跑完整 `main()`） | 账号不一致 → **不建库、不推送**；一致 → 建库拿到正确的 `name`/`private`、`live` 列表正确、推送用的是令牌解析出的账号、且确实走了 refs 核对 |
| 双平台不同名账号（§10b） | `--platform both` + 两个 `--account` → 两边都建库、都推送、各自用自己账号、没有"账号对不上"；只给一个地址 → 另一边**只告警不拦**；给错一个 → **只拦错的那个，另一个照推** |
| 集成边界 | 仓库已存在 → 提示"复用"且照推；建库失败（422 实名）→ 给出实名提示且不推送 |

**过程中修掉的三个真 bug**（都不是测试的问题）：
1. **向导死循环**：令牌那一问若一直拿不到有效值，会无限刷屏永不退出。
   修复：所有追问加重试上限 `MAX_ASK_RETRY=5`，超限即退出并给出可用的命令行替代。
2. **`--account` 单独给出时崩溃**：`wizard_collect` 依赖调用方预解析并传 `expect_login`，
   漏传就 `KeyError: None`。修复：函数内部自行解析 `--account`，不再依赖调用方。
3. **SSH 简写解析错**：`git@gitee.com:zhangsan.git` 把账号名解析成 `zhangsan.git`。
   修复：单段且以 `.git` 结尾时，那一段是账号名。

### 7.5 首次真实端到端（2026-09-22，真账号真令牌）

这是**第一条用真实令牌跑通的记录**，前面 7.1–7.4 全是本地桩化/裸仓库。

| 步骤 | 结果 |
|---|---|
| `--set-token gitee=…` | 令牌有效，账号识别为 `zhangsan` |
| 建库 | ✅ `POST /user/repos` 成功，`auto_init=false` 的私有仓库 |
| 提交 + 推送 | ✅ 2 个文件（`helloai.py`、`.gitignore`），8.8 秒 |
| 远端核对 | ✅ `git ls-remote` 的 SHA == 本地 HEAD |
| 事后查 API | `full_name=zhangsan/helloai-demo`、`private=True`、`default_branch=main` |

**这次暴露并修掉的三个真问题：**

1. **`--user-name` / `--user-email` 会顺带跑完整同步流程。** 它存完身份没 return，
   而 `--dir` 为空就退化成"当前目录"，于是把**工作区根目录**初始化成了 git 仓库
   （报错 `helloai/ does not have a commit checked out`）。修复：纯设置操作存完即返回；
   另外非交互下未指定 `--dir` 时明确打印"按当前目录处理"。
2. **推送后 `git status` 显示 `[gone]`。** 因为推送走显式 URL，git 不建 remote-tracking
   引用；接着本机工作区里 `git fetch`/`update-ref` 写 `refs/remotes/**` 又静默失败
   （§2 第 15 条）。修复：新增 `ensure_remote_ref()`——先 fetch 再**验证引用在不在**，
   不在就直接写 ref 文件。修复后 `git status` 恢复成 `## main...origin/main`。
3. **仓库名被默认成目录名。** 是我操作时漏了 `--name`（不是脚本 bug），
   用 `PATCH /api/v5/repos/{owner}/{repo}`（`name` 必填、`path` 决定 URL）改名后，
   本地 remote 与跟踪引用一并对齐。

**顺带确认的边界：** `PATCH` 改仓库名能用（`name` + `path`，form 传 `access_token`），
但这**不属于** git-sync 的能力范围（§0 写明"不改仓库设置"）——本次是为修正操作失误
临时做的。

### 7.6 第二次真实演练：改文件 + 删文件（2026-09-22 22:51–22:53）

用户要求"演习一遍"：在 `helloai/` 里新建 `helloai2.py`、两个平台都更新、再删掉旧的
`helloai.py`。**分两轮跑**，正好把"新增"和"删除"两类改动都走一遍（沿用同一套远端与令牌，
两个平台都是直连成功，没有触发清代理/绑 IP 的降级）。

| 轮次 | 本地操作 | 结果 |
|---|---|---|
| 1 | 新建 `helloai2.py` | Gitee 10.7 秒 · GitHub 14.8 秒，`git ls-remote` 核对 SHA 均一致 |
| 2 | 删除 `helloai.py`（`git add -A` 带上删除） | Gitee 10.3 秒 · GitHub 14.0 秒，同上 |
| 复核 | 两个平台各调 API 查根目录 + 提交 | 都只剩 `.gitignore` + `helloai2.py`，HEAD 同为 `5fe9459a` |

**这一轮暴露并修掉的两个真问题：**

1. **重复远端名（真 bug，已改代码）。** 详见 §2 第 19 条：`--platform github` 单跑时
   没复用已指向 GitHub 的 `mirror`，多加了个 `github-remote`。修复后重跑输出变成
   `沿用已有远端 mirror`，那条多余的远端已 `git remote remove` 清掉，
   现在仓库是干净的 `origin`(gitee) + `mirror`(github)。判据改为
   `remote_matches(url, platform, repo, login)`——**host + 属主 + 仓库名精确匹配**。
2. **删除项被报成「将推送 1 个文件，合计 0 B」（措辞，已改代码）。** 删除是改动但体积为 0，
   读起来像"没有东西要推"。现在单独一行：「其中 1 项是删除（b.txt）——远端对应文件会一并消失，
   这是故意的」。新增 `staged_deletions()`（`git diff --cached --name-only -z --diff-filter=D`），
   并在 `test_robustness.py` §8 用真实仓库验证（删文件 → 暂存 → 能识别出 `['b.txt']`）。

**复述一条既有约束（不是 bug）：** `--name` 是全局的，两个平台共用同一个仓库名。
本项目 Gitee 叫 `helloai-demo`、GitHub 叫 `helloai`，所以**只能分两次跑、各带各的 `--name`**；
若图省事用 `--platform both`，会在 Gitee 上新建一个叫 `helloai` 的仓库（真实的副作用）。
另外 `--account` 每次都得带上对应平台的地址，否则 §1.3 的账号核对那一步就失去意义。

**测试规模：144 → 161 条全过**（`test_robustness.py` 26 → 44，`test_wizard.py` 86，
`test_repo_create.py` 31）。

### 7.7 第三次：全新项目从零走完（2026-09-23 00:15–00:45）

用户说"咱们来实验一下，从头来"。于是造了一个**全新项目**（不是改已有仓库）：
`expotool/expotool.py` —— 曝光等效换算小工具（纯标准库，EV 归一化到 ISO 100，
支持 ND 档数与手持安全提示）。目标是把「新文件夹 → 新建远端库 → 推送」这条
最完整的路径跑一遍，**且一次运行推两个平台**。

| 步骤 | 结果 |
|---|---|
| `--dry-run` 体检 | 2 个文件 / 4.9 KB / 未发现密钥，计划：`main` · gitee+github · 私有 |
| 真推（第一次） | **Gitee ✅ 新建并推送成功**；**GitHub ❌ `Recv failure: Connection was reset`** |
| 诊断 | 见下 |
| 重试 | ✅ **13.7 秒**（第一级直连就过），两边 `git ls-remote` SHA 一致 |
| 复核（API） | 两个平台根目录均为 `.gitignore` + `expotool.py`，HEAD 同为 `ef8e5fb8` |

**这一轮暴露并修掉的两个真问题：**

1. **`--platform both` 配单个 `--account` 会误杀一个平台（真 bug）。**
   `expect_login` 是单值，而两个平台账号名不同 → 必然有一个被判"账号对不上"跳过。
   也就是说**"一次运行推两个平台"在用户的账号形态下根本不可能成功**。
   修复：`expect_logins` 改成 `{平台: 账号名}`，`--account` 支持 `action="append"`；
   没给地址的平台**明确告警"跳过账号归属核对"**（不静默、也不误拦）。
   `test_wizard.py` §10b 加了 10 条断言覆盖（含"只拦对不上的那个、另一个照推"）。

2. **「绑 IP」兜底绑的是写死的老段（真 bug）。** 诊断 GitHub 推不上去时发现，
   脚本绑的是 `140.82.114.4`（美国段），而本机 DNS 实际解析到 `20.205.243.166`（亚太段）
   ——这一级不但没兜底，还白等一次超时。改为 `github_ips()`：**DNS 结果优先**，
   写死段只在 DNS 全线拿不到时兜底。`test_robustness.py` §9 加了 10 条断言。

**顺带查清的一个环境事实（已做成第 2 级兜底）**：本机到 `github.com` 的 **HTTP/2 会被
中途 RST**，同一分钟内切 `http.version=HTTP/1.1` 就能完成握手，而 `api.github.com` 走
HTTP/2 一直正常。完整对照表见 §2 第 21 条。⚠️ 但要诚实：**紧接着重试时 HTTP/2 又通了**，
链路在抖——所以这是"有效兜底"，不是"根因修复"。

**测试规模：161 → 183 条全过**（`test_robustness.py` 44 → 56，`test_wizard.py` 86 → 96，
`test_repo_create.py` 31）。版本 v1.6.0 → v1.7.0（后续 v1.8.0 分发清理、**v1.9.0** 见 §7.8）。

---

### 7.8 第四次：把自己推上去（2026-09-23），顺手抓出可见性 bug

**任务**：清掉技能目录里的个人信息 → 写使用者 README → 打包 → 推成公开仓库。
这也是第一次**真跑 `--public`**（此前所有仓库都用的默认私有）。

| 步骤 | 结果 |
|---|---|
| 敏感清理 | 账号名 94 处 → 占位名；`__pycache__` 删除；本机路径隐去 |
| 打包 | `git-sync-v1.8.0.zip`，8 文件 / 76.2 KB |
| 演练体检 | 8 文件 / 200.1 KB / 无密钥 |
| 真推两平台 | Gitee ✅ 新建 · GitHub ✅ 新建，24.8 秒，均直连 |
| API 复核 | 文件清单与 HEAD `b1253142` 三方一致 |

**抓到的真 bug：`--public` 在 Gitee 上完全失效。**

复核时发现 **GitHub 是公开、Gitee 是私有**——而我是同一个 `--public` 推的。
排查路径：先怀疑表单布尔编码（`urlencode` 把 `False` 编成 `"False"`），
读代码发现 `http_json()` 已经做了 `"true"/"false"` 转换，**这条路是通的**；
于是改用**探针仓库**直接问接口本身（每个探针建完即删，`DELETE` 回 204、
再查回 404 确认删净）——5 种写法全建成私有，**证明参数根本没被读**。
再测 PATCH：带 `name`+`path` 时 200 成功、仓库立刻变公开。

→ 修复：新增 `align_visibility()` / `repo_visibility()` / `api_set_visibility()`，
建完**读回来核对**（`test_repo_create.py` §8 加 22 条断言，本轮 31 → 53）。
已存在而同名的 Gitee 仓库用 PATCH 补成公开（本次的 `git-sync` 就是这么修的）。

**修复过程中又自己踩了一个坑（值得记）**：给 `main()` 加了可见性核对这一步之后，
`test_wizard.py` 只桩了 `api_create_repo`、**漏桩 `align_visibility`**，
于是测试拿**假令牌去打了真实的 gitee.com / api.github.com**。
**测试照样全绿**（请求失败后降级成告警，断言全不涉及它），但测试已经不再 hermetic：
网络一抖结论就变，而且每次跑都在对外发请求。
处置：桩掉 `align_visibility`，并在 `run_main()` 里把 `G.http_json` 换成一个
**会直接抛异常的守卫**——任何"没打全的桩"都会立刻炸出来，而不是悄悄降级。

→ 通用教训：**新增一个对外调用步骤时，测试里的桩必须同步补**，
否则"测试全绿"这个信号会变成假的。守卫要**抛异常**，不能只记录。

**测试规模：183 → 212 条全过**（`test_robustness.py` 56，`test_wizard.py` 96 → 103，
`test_repo_create.py` 31 → 53）。版本 v1.8.0 → **v1.9.0**。

**同一轮里紧接着又踩到第二个真 bug：GitHub 推不上去（v1.9.1）。**

推完 v1.9.0 并复核时，**Gitee 成功、GitHub 失败**：
`Failed to connect to github.com:443 after 21045 ms`，脚本报
「已自动试过清代理 / 绑 IP 仍失败」。但我不信这句话，去实测：

1. `api.github.com` **0.27 秒就回 200** → 不是全链路断，是 `github.com:443` 这个端点的事。
2. 逐个探候选 IP（清代理 + HTTP/1.1 + `http.curloptResolve`）：
   `140.82.121.4` → `expected flush after ref listing`，
   其余几个 → 挂住/超时或 RST。
3. ⚠️ 关键判断：`expected flush after ref listing` 是**"收到一半"**，
   **不能**判定为不通。重试 3 次——**3/3 全通**。这一个 IP 就能用。
4. 回头看代码：`github_ips()` 是 `return dns_ips[:1]`，调用处也只取 1 个 →
   "绑 IP"这一级**只有一次机会**。当天 DNS 给的 `20.205.243.166` 恰好挂住，
   整级就废了。**同链路里明明有一个能通的 IP，代码从没试它。**

→ 修复：`github_ips(limit=4)` 返回「DNS 结果 + 候选段」（去重、限量）并**逐个试**；
`PUSH_TOTAL_BUDGET` 300 → 420 秒（给"每个不通 IP 各白等 ~21 秒"留预算）；
候选段里补入实测可通的 `140.82.121.4`。
`test_robustness.py` §9 改为钉死"**必须给多个候选**"，56 → 58 条。

**这条最值得记的不是代码，是那句报错**：脚本说"已自动试过清代理 / 绑 IP 仍失败"，
听起来像"办法都用尽了"，实际是"只试了一个地址"。
**报错信息比实情悲观，会把排查带偏**——我确实差点先去查代理和令牌。
→ 教训：**"已尝试全部方案"这种措辞，只有在真的枚举完了才能写。**

**测试规模：212 → 214 条全过**（`test_robustness.py` 56 → 58，`test_wizard.py` 103，
`test_repo_create.py` 53）。版本 v1.9.0 → **v1.9.1**。

### 7.9 第五次：定许可证 + 收敛技能（2026-09-23）

**任务**：给仓库定许可证 → 把四个并列的 git 类技能卸掉 → 再推一轮。

| 步骤 | 结果 |
|---|---|
| 版权署名 | 用户当场指定 `人间小土鸡`。**没替他选，也没敢猜** |
| 加 `LICENSE` | MIT 全文，一字未改 |
| 卸技能 | `github-workflow` / `git-workflow-and-versioning` / `code-review-report` / `github-workflow__skillhub` 全部移出技能目录 |
| 备份 | `backup/skills-removed-2026-09-23.zip`，52 文件 / 182 KB，`testzip()` 完整性 OK |

**卸技能 ≠ 删代码，优先「移出目录」。** 技能是靠**目录扫描**发现的
（`~/.workbuddy/skills/<name>/SKILL.md`），本机**没有"已安装技能登记表"**——
查过 `workbuddy.db`，只有 `sessions`/`workspaces`/`automations` 等表，**没有 skills 表**；
`_skillhub_meta.json` 这类元数据是**跟着技能目录走的**，删目录即一并消失。
→ 结论：**把目录移走就等于卸载，且完全可逆**。本次前三个是删的（已备份），
第四个 `github-workflow__skillhub` 因**批量删除守卫**（单轮 50 项阈值）被拦，
改用 `shutil.move()` 移到 backup 目录——同样卸载，但**随时能还原**。
→ 教训：**卸载技能默认走"移出目录"，不要一上来就 `rmtree`**；
真要删也别用 `rm -rf`，用 Python 侧逐个显式列名，避免通配符误伤相邻技能目录。

**许可证这件事的边界**：README 里先如实写"未附带"，**不替用户选**；
用户表态后再落 `LICENSE` 文件并同步 README。**署名必须问**——写错的版权声明
比不写还麻烦。版本 v1.9.1 → **v1.10.0**（功能：许可证 + 技能收敛，214 条测试保持全过）。

**顺手补掉 `check_clean.py` 一个真实漏检**：加完 `LICENSE` 后跑自查，
提示仍是「扫了 **7** 个文本文件」——数字没变，说明**新文件根本没进扫描范围**。
根因：它以**扩展名**为唯一判据（`TEXT_EXT`），而 `LICENSE` / `.gitignore` /
`COPYING` 这类**没有扩展名**，整份跳过。而 `LICENSE` 恰恰是**写版权人姓名和
联系邮箱**的地方——最该被扫的文件反而是盲区。
→ 补 `is_text_file()`：扩展名命中 **或** 文件名命中 `TEXT_BARE_NAMES` 才扫。
→ 通用教训：**"扫了多少个文件"这个数字要看**。加了文件而计数不变，
就是新文件被规则挡在门外了——**"干净"结论只覆盖它真正扫过的那部分**。

**对外的一句话介绍（用户提的）**：

> **「和 AI 聊两句，git 就完事了。」**

用户原话是「和ai聊两句就git完了」，只动了标点与大小写。这句好在**说的是"你不用会 git"，
而不是"我功能多"**——本技能真正的门槛消除就在这里。已落到三处并保持一致：
`README.md` 首句、两个平台的仓库简介（API `PATCH` 改完读回复核）、本文件顶部。
→ **一句话介绍必须只有一份。** 三处各写各的，等于没有一句话介绍。
版本 v1.10.0 → **v1.10.1**（纯文档：口号 + 三处同步，脚本逻辑未动）。

### 7.10 第六次：GitHub 报「认证失败」，其实是代理（2026-09-23，v1.11.0）

推 v1.10.1 时 **Gitee 成功、GitHub 失败**，报 `fatal: Authentication failed for
'https://github.com/sanjicook074-cell/git-sync.git/'`。

**第一反应是"令牌被撤销了"——但同一枚令牌几分钟前刚用它 PATCH 成功过
api.github.com 的仓库简介（读回复核过），而且 v1.10.0 那轮还能推。**
令牌没动过、权限没动过，说明**这句报错在撒谎**。

不信报错、去分档实测（`--show-config` 拿令牌，打印时脱敏）：

| 探测档 | 原文 |
|---|---|
| 直连 | `unable to access ...: **CONNECT tunnel failed, response 502**` |
| 清代理 | `Failed to connect to github.com:443 after 21103 ms` |
| 清代理 + HTTP/1.1 | 同上（21107 ms） |
| 清代理 + 绑 IP 140.82.121.4 + HTTP/1.1 | `Recv failure: Connection was reset` |

→ 直连那档**根本没连到 GitHub**：本地代理回的是 **502**。
这就解释了一切：**代理拒绝 CONNECT 时，git 会把 URL 里带的凭据拿去当「代理」凭据试**，
被拒后打印的是 `Authentication failed`——**一句把"代理坏了"说成"你令牌不对"的报错**。

**而代码把"认证失败"归为权限类错误 → 立即 break → 阶梯在第 1 级就断了，
"清代理"那三档一次都没试。**（本轮 4 档实测里，第 2 档清代理确实也连不上，
但**这是"当时确实不通"，不是"试都没试"**——两回事。上一轮同样情形下，
清代理+绑 IP 是通的。）

→ 修复（v1.11.0）：
1. 新增 `AUTH_ERROR_HINTS` / `is_auth_error()` / `has_proxy_env()`；
2. `push_platform()`：**认证类错误 + 环境里有代理变量 → 不停，继续降级**；
   没有代理还报认证失败 → 仍是权限问题，立即停（不放弃原有的"不盲目重试"）；
3. 最终仍失败时，错误文本后补一句"这条认证失败可能是代理冒充的，先查代理别急着换令牌"，
   **不改变失败结论**。
`test_robustness.py` 新增 §10 共 9 条钉住：有代理必须继续升到绑 IP、没代理必须 1 级就停、
结论仍是失败、解释文案只在有代理时出现。

**写测试时自己又踩一脚**：断言写成了「`has_proxy_env()` 为假」，
默认了"环境里没代理"——而本机**本来就配着代理**（不然也不需要"清代理"这一级），
于是测试红了。改成先显式清空代理变量再断言。
→ 教训：**测试不要依赖宿主环境的状态**，要用就先把它显式设成已知值。

**测试规模：214 → 223 条全过**（`test_robustness.py` 58 → 67）。
版本 v1.10.1 → **v1.11.0**。

**这条最值得记的，是"报错在撒谎"这件事有多像真的。**
`Authentication failed` 读起来是一个明确的、可执行的结论（"去换令牌"），
而它实际来自一个完全无关的组件（本地代理）。**越是具体、越像结论的报错，越要先验一遍。**
分辨办法很便宜：**报错说 A 组件有问题，就去看同链路的其他入口是不是也不通**
（这次是 `api.github.com` 秒回 200）——不通，报错就在栽赃。

---

## 8. 分发 / 分享这个技能（2026-09-23 实测）

技能就是**纯文件**：`SKILL.md` + `scripts/*.py`，零第三方依赖、纯标准库、Python 3.8+ 可跑。
所以"给别人用"没有技术障碍——把整个 `git-sync/` 目录拷进对方的 `~/.workbuddy/skills/` 即可
（用户级技能，对该机所有项目生效）。

**但没有自助上架通道。** 本机可用的市场工具（`workbuddy_marketplace_skill`）只有
`search` / `install` 两个动作，**没有 submit / publish**；官方文档里也没找到面向
个人开发者的技能提交流程。想进推荐市场得走官方渠道，别向用户承诺"我可以帮你提交"。

**分享前要清什么**（已查过并**已完成**，不是推测）：

| 清什么 | 实际情况 | 为什么 |
|---|---|---|
| `scripts/__pycache__/` | 3 个 `.pyc`，已删除 | 编译缓存，不该分发；`.pyc` 里同样含明文串（grep 得到，肉眼看不见） |
| 文档 / 测试里的**真实账号名** | 总计 **94 处**（主账号名 80 + 另一平台账号名 11 + 大小写变体 3），已全部换成占位名 `zhangsan` / `lisi` | 账号名是身份标识；占位名不影响任何断言 |
| 本机用户目录路径 | 1 处（§2 第 1 条的 PortableGit 路径），已隐去用户名与版本号 | `C:\Users\<名字>\...` 会暴露本机用户名 |
| 令牌 / 邮箱 / 昵称 | **本来就没有** | 令牌在 `~/.workbuddy/git-sync.json`（权限 600），不在技能目录内 |

**替换时有个坑**：原账号名在测试里出现了**大小写变体**，而且有断言专门验证
"两个账号名只是大小写不同 → 不算冲突"（比如 `AliceBob` vs `alicebob`）。
所以替换必须**先长后短**：先换掉带大写字母的变体，最后才换全小写的基准名。
顺序反了，几个变体会被压成同一个字符串，那条断言就**静默地失去意义**了——
测试照样全绿，但它已经不再检验任何东西。

**每次分发前都跑一遍自查**，别靠记忆：
```bash
python scripts/check_clean.py            # 退出码 0 = 干净；非 0 = 有残留
python scripts/check_clean.py --dir .    # 也可指定目录
python scripts/check_clean.py --deny 我的账号名     # 额外拉黑自定义字串
```
它会扫：高熵令牌形状、邮箱、手机号、`C:\Users\<名>` 路径、`__pycache__` 残留在内的常见夹带物。
`__pycache__` 属于"运行就会再生成"的东西，所以打包前跑、`.gitignore` 里也记得排除。

**这个自查脚本救过一次场（2026-09-23，值得记）**：清理完成、94 处替换做完、测试 183 条
全绿之后，把原账号名当 `--deny` 跑了一遍——**它报出 4 处残留**，全在 `SKILL.md` 自己身上：
我在写"已清理了哪些账号名"这张表时，**把账号名原样写回去了**。
人眼复查时这段读起来完全合理（"哦，这是在记录清理了什么"），**看不出问题**；
只有逐字比对才能发现"记录清理的文字本身又泄漏了一次"。
→ 教训：**"已清理"这个结论必须由命令给出，不能由写文档的人自己宣布。**

## 9. 面向使用者的 README（2026-09-23）

`README.md` 与 `SKILL.md` 是**两份不同读者的文档**，别合并：

| 文件 | 读者 | 写什么 |
|---|---|---|
| `SKILL.md`（本文件） | **智能体** | 能力边界、调用契约、环境硬约束、故障对照表、实测记录。可以有"本机实测""踩过的坑"这类只有我自己关心的内容 |
| `README.md` | **人**（拿到技能的人 / 未来的自己） | 这是什么、装到哪、怎么用、怎么拿令牌、出事怎么查、它怎么保护你 |

README 里刻意写进去的几样（都是分发时真正会被问到的）：
- **它替代了什么**——把手工 7 步列出来，让人一眼看懂价值，而不是只罗列功能。
- **完整排查表**：每条都是本机真实遇到过的报错（`Incorrect username or password`、
  422 实名认证、`[gone]`、`Connection was reset`…），标清原因和对策。
- **"静默失败"这条要写给人看**：git 退出码会骗人，所以脚本拿 `ls-remote` 比 SHA。
  这是用户最该知道的一条设计取舍。
- **已知限制单独一节**：特别是"§2 那些环境事实是**开发机**的，不是通用规律"——
  别人机器上 PortableGit 路径、凭据助手行为都不一样，不写清就会误导。
- **许可证不替用户选**：先如实写"未附带"（未加许可证 = 默认保留所有权利），等用户表态。
  2026-09-23 用户定为 **MIT**，署名由用户当场给出（`人间小土鸡`）——
  **署名不是能猜的东西**，问一句的成本远低于写错的成本。
- **目录结构一节要把 `LICENSE` 列出来**：这是别人 clone 下来一眼确认授权的地方，
  只写在末尾的「许可」一节里不够醒目。


