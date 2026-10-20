# GitHub Notes Skill and MCP

用独立 GitHub 仓库保存调研和开发笔记。包含一个可全局安装的 Codex Skill，以及一个复用 GitHub CLI 授权的轻量笔记 MCP。

首次使用检查授权、分析候选仓库、选择并绑定笔记目录；没有合适仓库时按用户要求创建私有仓库。后续直接保存、搜索、读取和补充笔记。通用源码可公开分享，每个用户独立授权并使用自己的笔记库。

## 快速安装

准备 Python 3.9+、GitHub CLI 和 Codex CLI：

```sh
git clone https://github.com/guojianlan/github-notes-skill.git
cd github-notes-skill
python3 install.py
gh auth login --hostname github.com --git-protocol https --web
```

Windows 可以用 `py -3 install.py`。授权已有效时不必重新登录。新开聊天或重启客户端加载 Skill/MCP，然后说：

> 使用 $github-notes 初始化我的笔记库，先看看是否已有合适的私有仓库。

> 使用 $github-notes 把这次调研保存到我的笔记库，保留来源和待验证步骤。

> 使用 $github-notes 查找我以前关于作业批改的调研。

> 使用 $github-notes 把这次验证结果补充到指定笔记。

## 架构

Skill 负责授权和仓库选择流程、笔记组织与回复；MCP 负责身份与权限检查、目录范围、GitHub API 读写、并发校验和提交后读回。

MCP 是本机 stdio 进程，GitHub 是远程存储和版本来源。每台电脑分别安装授权，共享同一个 GitHub 仓库。当前不提供托管 HTTP MCP，也不包含后台自动归档和离线同步队列。

代码只依赖 Python 标准库，通过 `gh api` 调用 github.com。Token 由 GitHub CLI 管理，不写入共享代码、笔记或绑定配置。

## 工具

| 工具 | 用途 |
| --- | --- |
| `notes_status` | 检查当前 GitHub 身份和笔记库绑定 |
| `notes_discover` | 按名称和描述发现候选，检查远程笔记目录标记 |
| `notes_inspect_repository` | 分析权限、可见性、默认分支、目录和约定文件 |
| `notes_read_repository_file` | 读取首次设置需要的 README、AGENTS 等约定 |
| `notes_bind_repository` | 在本机记住用户选择的仓库与目录 |
| `notes_create_repository` | 按需创建仓库，默认私有 |
| `notes_initialize` | 新增 `.github-notes.json`，帮助其他电脑识别笔记目录 |
| `notes_list` / `notes_search` / `notes_get` | 分页列表、检索正文、读取笔记及 SHA |
| `notes_create` | 新增 Markdown，拒绝覆盖已有文件 |
| `notes_update` / `notes_append` | 带预期 SHA 编辑或追加，拒绝过期版本 |

每次写入直接提交并读回校验。工具无删除、修改仓库可见性或执行笔记内命令的功能。搜索结果明确返回范围和游标；自动发现采用名称启发式，找不到时可以直接指定仓库。

## 安装位置与其他客户端

Skill 默认安装到 `${CODEX_HOME:-~/.codex}/skills/github-notes`；MCP 使用 `codex mcp add` 写入用户配置，保留已有服务。更新前备份同名 Skill；发现同名但不同配置的 MCP 时拒绝覆盖。

`python3 install.py --skip-mcp` 仅安装 Skill，并打印其他 MCP 客户端可用的配置。运行入口为：

```sh
python3 /absolute/path/to/github-notes/scripts/github_notes.py stdio
```

CLI 也可直接调用相同工具：

```sh
python3 skills/github-notes/scripts/github_notes.py status
python3 skills/github-notes/scripts/github_notes.py tools
python3 skills/github-notes/scripts/github_notes.py call notes_discover
```

对有参数的调用，将 JSON 写入临时文件，使用 `--json-file /path/to/arguments.json`；不要把笔记正文拼入 shell。

详细授权、跨电脑流程与限制见 [setup.md](skills/github-notes/references/setup.md)。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

单元测试使用内存中的模拟 GitHub，不创建真实仓库。真实 GitHub 验收记录见 [VALIDATION.md](VALIDATION.md)。

项目为 MIT。个人笔记及其引用资源的许可不由本项目许可证授予。
