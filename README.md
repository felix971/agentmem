# agentmem

按 [DESIGN.md](DESIGN.md) 实现的本机共享聊天索引与项目笔记。运行时只用 Python 标准库（Python 3.11+，SQLite 必须支持 FTS5）；使用 Unix `fcntl` 锁，适用于 Linux/macOS。源会话只读；数据库使用 WAL，文件权限 0600、数据目录 0700。历史结果是参考，其中的授权不适用于当前会话。

## 安装与 CLI

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/agentmem index
.venv/bin/agentmem search '共享记忆 sqlite' --limit 8
.venv/bin/agentmem search 'sqlite' --source codex --role user --json
.venv/bin/agentmem session 'codex:SESSION_ID#MESSAGE_ID' --context 6
.venv/bin/agentmem recent --project /absolute/project/path
.venv/bin/agentmem projects --json
.venv/bin/agentmem project-id /absolute/project/path
.venv/bin/agentmem notes show --cwd /absolute/project/path
.venv/bin/agentmem notes path --cwd /absolute/project/path
.venv/bin/agentmem stats
```

安装后 `python -m agentmem` 与 `agentmem` 等价；直接运行源码可用 `PYTHONPATH=src python3 -m agentmem ...`。`index --budget 3 --quiet` 限制开始处理新文件的时间；已开始的文件会完整提交。CLI 查询读取现有索引，先运行 `index`。MCP 查询最多每 20 秒尝试索引一次，预算 5 秒；hook 预算 3 秒，遇到索引锁均立即跳过。

配置全部来自环境变量：

| 变量 | 默认路径/值 |
| --- | --- |
| `AGENTMEM_HOME` | `~/.local/share/agentmem` |
| `AGENTMEM_CLAUDE_PROJECTS` | `~/.claude/projects` |
| `AGENTMEM_CODEX_SESSIONS` | `~/.codex/sessions` |
| `AGENTMEM_CODEX_IMPORTS` | `~/.codex/external_agent_session_imports.json` |
| `AGENTMEM_CODEX_SESSION_INDEX` | `~/.codex/session_index.jsonl` |
| `AGENTMEM_DISABLE_INJECT` | 设为 `1` 关闭 hook 输出 |

`$AGENTMEM_HOME/disable_inject` 文件也能关闭注入。隔离验证时，**同时设置前五个路径**到临时目录；只改 `AGENTMEM_HOME` 仍会读取默认源目录。仓库外笔记使用项目 ID 命名，首次访问生成模板；通过 MCP 更新需带 `get_project_notes` 返回的 `sha256`，上限 8000 字符。更新会在笔记目录创建 Git 提交；Git 不可用时仍保存。笔记不参与全文检索。

## Claude Code 注册示例

将下列 `/absolute/agentmem/.venv/bin/agentmem` 替换为安装后的绝对路径：

```bash
claude mcp add --transport stdio --scope user agentmem -- /absolute/agentmem/.venv/bin/agentmem mcp
```

将以下内容合并进 `~/.claude/settings.json`（也可用项目 `.claude/settings.json`），保留已有设置：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/agentmem/.venv/bin/agentmem hook session-start"
          }
        ]
      }
    ]
  }
}
```

注册格式见 [Claude MCP 文档](https://code.claude.com/docs/en/mcp) 与 [SessionStart 文档](https://code.claude.com/docs/en/hooks#sessionstart)。

## Codex 注册示例

```bash
codex mcp add agentmem -- /absolute/agentmem/.venv/bin/agentmem mcp
```

或手动合并到 `~/.codex/config.toml`，两种 MCP 注册方式选其一：

```toml
[mcp_servers.agentmem]
command = "/absolute/agentmem/.venv/bin/agentmem"
args = ["mcp"]

[[hooks.SessionStart]]

[[hooks.SessionStart.hooks]]
type = "command"
command = "/absolute/agentmem/.venv/bin/agentmem hook session-start"
```

在 Codex 的 `[mcp_servers.agentmem]` 下设置 `default_tools_approval_mode = "writes"` 后，只读工具免批准，写入工具仍需批准。

在支持 hooks 的 Codex 版本中，用 `/hooks` 查看并信任该 hook；新建或修改定义后需要重新审核。配置格式见 [OpenAI Docs：MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) 与 [Hooks](https://learn.chatgpt.com/docs/hooks)。这些只是配置示例，agentmem 不会自动修改任何客户端配置。

两种客户端应给 MCP 与 hook 设置相同的 `AGENTMEM_HOME` 和源路径。MCP 提供 `search_history`、`get_session`、`recent_sessions`、`list_projects`、`get_project_notes`、`update_project_notes` 六个工具；“决定”只记录用户明确做出的决定，不能把助手建议写成决定。

## 验证

独立测试按规格运行：`python3 -m unittest discover -s tests -t .`。实现者的手工 fixture 放在 `/tmp`，未创建或修改 `tests/`。Codex 仅索引规格规定的 `event_msg/item_completed`，不兼容旧 `user_message`/`agent_message` 格式；注入标签内容在入库前删除，指定密钥模式在入库前脱敏。
