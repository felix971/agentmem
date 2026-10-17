agentmem 黑盒验收测试说明

唯一规格：仓库 DESIGN.md。
运行：python3 -m unittest discover -s tests -t . -v

隔离与边界
  每个 TestCase 都新建 TemporaryDirectory；五个 AGENTMEM 路径、HOME、XDG、TMPDIR、Git 配置和提交身份均隔离。
  所有产品调用都是 python3 -m agentmem 子进程，PYTHONPATH 指向仓库 src，禁止写字节码。
  不导入 agentmem，不读取实现源码，不依赖内部 SQLite 表结构。
  search 解析 --json；session 的 JSON 详细 schema 不明确，改用第 9 节明确规定的消息头格式。
  MCP 使用持续运行的子进程和逐行 JSON-RPC 交互，带超时、退出诊断和清理。
  并发测试先启动两个 Popen，再等待结果，并用 search --json 核对总数和唯一性。
  脱敏存储检查通过公开 session 原文接口完成；fixture 足够短，不会被 2000 字符正文上限隐藏。

验收项与测试方法

12.1 Claude 解析 — tests/test_claude.py
  字符串、text 块拼接、标签清洗、isMeta/tool_result/sidechain/subagents 排除、助手块、标题优先级。
  ClaudeParsingTests.test_string_user_content
  ClaudeParsingTests.test_user_text_blocks_joined_and_nontext_ignored
  ClaudeParsingTests.test_system_reminder_and_other_paired_tags_are_removed
  ClaudeParsingTests.test_messages_empty_after_cleaning_are_not_indexed
  ClaudeParsingTests.test_meta_tool_only_sidechain_and_other_record_types_are_skipped
  ClaudeParsingTests.test_subagents_directory_is_excluded
  ClaudeParsingTests.test_assistant_takes_only_text_blocks
  ClaudeParsingTests.test_custom_title_wins_and_latest_same_kind_wins
  ClaudeParsingTests.test_latest_ai_title_used_without_custom_title
  ClaudeParsingTests.test_missing_uuid_uses_stable_raw_line_identity

12.2 Codex 解析 — tests/test_codex.py
  UserMessage/AgentMessage、注入排除、两种 subagent 标记、imported 保留空会话、fork 去重、标题刷新、增量解析状态。
  CodexParsingTests.test_user_and_agent_message_text_variants
  CodexParsingTests.test_response_items_legacy_events_and_commands_are_ignored
  CodexParsingTests.test_first_session_meta_wins
  CodexParsingTests.test_both_subagent_markers_skip_messages_but_keep_sessions
  CodexParsingTests.test_imported_thread_skips_messages_but_keeps_session
  CodexParsingTests.test_missing_imports_file_is_empty_set
  CodexParsingTests.test_corrupt_imports_file_is_empty_set
  CodexParsingTests.test_fork_parent_thread_copies_do_not_duplicate
  CodexParsingTests.test_session_index_last_title_and_refresh_without_rollout_change
  CodexParsingTests.test_turn_context_and_thread_state_survive_incremental_reads
  CodexParsingTests.test_cleaning_applies_to_codex_too

12.3 增量与容错 — tests/test_index.py
  追加、跨 UTF-8 字节的残行、坏 JSON 计数、重复运行、截断、inode 替换、归档，以及两个同时启动的 index 进程。
  IncrementalIndexTests.test_report_no_changes_and_quiet_mode
  IncrementalIndexTests.test_append_adds_only_new_messages
  IncrementalIndexTests.test_partial_utf8_line_waits_for_final_newline
  IncrementalIndexTests.test_bad_complete_json_is_counted_blank_lines_are_not
  IncrementalIndexTests.test_truncation_resets_offset_without_duplicates_or_deletion
  IncrementalIndexTests.test_inode_replacement_restarts_even_when_file_is_larger
  IncrementalIndexTests.test_deleted_source_keeps_archived_messages
  IncrementalIndexTests.test_two_concurrent_index_processes_do_not_crash_or_duplicate

12.4 脱敏 — tests/test_redaction.py
  第 5 节全部 11 类及前缀/引号变体；两种来源均验证替换标记、session 不暴露原值、search 查不到敏感值。
  RedactionTests.test_private_key
  RedactionTests.test_anthropic_key_before_openai_pattern
  RedactionTests.test_openai_plain_and_project_keys
  RedactionTests.test_all_github_prefixes_and_fine_grained_pat
  RedactionTests.test_aws_key
  RedactionTests.test_all_slack_token_prefixes
  RedactionTests.test_google_key
  RedactionTests.test_huggingface_token
  RedactionTests.test_jwt
  RedactionTests.test_case_insensitive_bearer
  RedactionTests.test_assignment_aliases_preserve_keys_and_separators
  RedactionTests.test_assignment_quoted_values

12.5 中文检索 — tests/test_search.py
  四条指定语义、中英 AND/OR、单字兜底、FTS 字符、NFKC、项目/来源/角色过滤、ref 回溯与 around、前缀歧义。
  SearchTests.test_chinese_memory_bigram_matches
  SearchTests.test_chinese_multi_bigram_phrase_matches
  SearchTests.test_nonadjacent_chinese_characters_do_not_match
  SearchTests.test_english_case_insensitive
  SearchTests.test_mixed_chinese_english_query_uses_and_when_possible
  SearchTests.test_and_without_results_falls_back_to_or
  SearchTests.test_single_cjk_character_like_fallback
  SearchTests.test_fts_metacharacters_never_become_syntax
  SearchTests.test_nfkc_normalization
  SearchTests.test_project_source_and_role_filters
  SearchTests.test_search_ref_roundtrip_around_and_session_limit
  SearchTests.test_ambiguous_session_prefix_reports_candidates
  SearchTests.test_unknown_session_and_unknown_project_are_errors
  SearchTests.test_equal_relevance_is_sorted_by_timestamp_descending
  SearchTests.test_text_output_marks_history_as_untrusted_and_no_results
  SearchTests.test_cli_invalid_option_returns_one_on_stderr

12.6 项目 ID — tests/test_projects.py
  真实临时 Git 仓库中的两个新增 worktree 和 clone；普通目录、不存在目录、symlink。
  ProjectIdentityTests.test_two_worktrees_and_clone_share_root_commit_project_id
  ProjectIdentityTests.test_existing_non_git_directory_uses_real_absolute_path
  ProjectIdentityTests.test_nonexistent_directory_uses_normalized_absolute_path
  ProjectIdentityTests.test_existing_directory_symlink_resolves_to_same_path_id

12.7 笔记 — tests/test_notes.py
  模板及命名、get 的 SHA、正确 SHA 更新与 Git 提交、错误/过期 SHA 拒绝、8000 字符限制、不进入 FTS。
  ProjectNotesTests.test_first_access_creates_specified_template_path_and_git_repo
  ProjectNotesTests.test_get_notes_returns_content_and_actual_sha256
  ProjectNotesTests.test_matching_sha_updates_file_and_creates_commit
  ProjectNotesTests.test_wrong_sha_refuses_write_and_returns_current_content_and_hash
  ProjectNotesTests.test_second_client_with_stale_sha_cannot_overwrite_first
  ProjectNotesTests.test_content_limit_is_8000_characters_not_utf8_bytes
  ProjectNotesTests.test_notes_do_not_enter_history_search

12.8 Hook — tests/test_hook.py
  JSON/包裹/长度/当前会话排除、最近五个跨工具会话、两种关闭方式、垃圾 stdin、无信息、自我强化防护、长上下文和锁竞争。
  SessionStartHookTests.test_json_wrapper_length_recent_five_cross_tool_and_exclude_current
  SessionStartHookTests.test_no_information_is_silent_and_does_not_create_notes
  SessionStartHookTests.test_environment_disable_switch
  SessionStartHookTests.test_disable_file_switch
  SessionStartHookTests.test_garbage_empty_and_nonobject_stdin_are_silent_success
  SessionStartHookTests.test_missing_cwd_is_silent_success
  SessionStartHookTests.test_hook_lazily_indexes_fresh_sources
  SessionStartHookTests.test_reindexed_injected_context_does_not_become_new_evidence
  SessionStartHookTests.test_heading_only_notes_do_not_count_as_information
  SessionStartHookTests.test_notes_body_is_injected_and_truncated
  SessionStartHookTests.test_total_context_limit_with_oversized_titles_and_notes
  SessionStartHookTests.test_held_index_lock_uses_existing_data_without_waiting
  SessionStartHookTests.test_subagent_and_imported_sessions_are_not_injected

12.9 MCP — tests/test_mcp.py
  真实 stdio 握手、六工具、正常调用、未知方法、坏 JSON 后续请求、batch 拒绝、通知无回应、参数 isError、懒索引和锁竞争。笔记两工具的成功路径另见 test_notes.py。
  MCPProtocolTests.test_initialize_negotiates_supported_versions_and_fallback
  MCPProtocolTests.test_initialize_does_not_index_on_startup
  MCPProtocolTests.test_tools_list_contains_exactly_six_object_schema_tools
  MCPProtocolTests.test_normal_history_tools_match_cli_text_and_lazily_index
  MCPProtocolTests.test_unknown_method_returns_method_not_found_and_continues
  MCPProtocolTests.test_bad_json_returns_parse_error_then_server_still_works
  MCPProtocolTests.test_batch_is_invalid_request_and_server_continues
  MCPProtocolTests.test_notifications_have_no_response
  MCPProtocolTests.test_invalid_tool_arguments_are_iserror_not_rpc_errors
  MCPProtocolTests.test_missing_session_tool_error_does_not_exit_server
  MCPProtocolTests.test_held_index_lock_serves_existing_data_without_indexing

12.10 权限 — tests/test_permissions.py
  子进程继承 umask 022，验证 HOME 0700、DB/锁/笔记 0600，以及 notes 目录 0700。
  PermissionsTests.test_home_database_lock_and_notes_are_private_even_with_parent_umask_022

第 7 节新增规则 — tests/test_project_rules.py
  1. Claude 同一会话从独立 Git 仓库 A cd 到 B，全部消息仍属于 A；按 B 路径过滤为空，session 显示初始 cwd。
     ProjectRuleTests.test_claude_cwd_change_keeps_all_messages_in_initial_git_project
  2. 先索引 cwd=A 的前半段，再追加 cwd=B 的后半段；跨 index 子进程仍固定归属 A，不丢消息、不重复。
     ProjectRuleTests.test_claude_incremental_cwd_change_keeps_new_messages_in_initial_project
  3. Codex 在首条消息前即出现 turn_context.cwd=B，仍使用 session_meta.cwd=A。
     ProjectRuleTests.test_codex_session_meta_cwd_wins_over_turn_context_before_first_message
     CodexParsingTests.test_turn_context_and_thread_state_survive_incremental_reads 已同步改为初始项目归属断言。
  4. CLI projects 与 MCP list_projects 都不列仅出现在后续 cwd、仅被解析、或仅有 subagent/imported 会话的项目；有消息或空 main 会话的项目仍列出。
     ProjectRuleTests.test_projects_and_mcp_hide_project_seen_only_in_later_cwd
     ProjectRuleTests.test_projects_and_mcp_include_empty_main_but_not_non_main_or_resolved_only
  5. repoX 与 repoX-wt 的 notes path 相同；先登记仓库或先登记 linked worktree，都保持首次名称及单份笔记。
     ProjectRuleTests.test_worktree_notes_share_path_and_keep_first_registered_repository_name
     ProjectRuleTests.test_worktree_registered_first_keeps_its_name_and_shared_notes_path
  6. 两种 subagent 标记分别与 imports 清单重叠；初次索引及追加后都不产生消息，但保留可查询的空会话；正常 main 对照仍能入库。
     ProjectRuleTests.test_subagent_thread_source_also_imported_produces_no_messages
     ProjectRuleTests.test_subagent_source_object_also_imported_produces_no_messages
  另测 Claude 首条带 cwd 的记录不是可索引消息，仍以该记录的 cwd 定项目。
     ProjectRuleTests.test_claude_first_cwd_record_not_first_indexed_message_sets_project
  A/B 使用不同的根提交内容并验证 project_id 不同，避免同秒创建的空提交碰巧相同导致假通过。
  归属断言完成后另加 B 的独立 main 会话作为过滤正对照，再验证按 B 的路径/ID/名称都查不到目标会话消息；不把“未登记项目应返回空数组”当作规格要求。后续 cwd-only 的项目列表用例不加此对照。
  登记顺序由两次顺序执行的 index 确定，不依赖文件时间戳排序巧合。

规格歧义与本测试的处理
  1. 第 9 节 --json 的列表和字段描述紧接 search；session/recent/projects 的独立 JSON schema 未给出。只对 search 断言字段，session 使用明确的文本格式，projects 检查列表数量和已知名称/ID而不绑定键名，同时核对 MCP 与 CLI 文本一致。
  2. 第 3.5 节要求跳过的 subagent 也写 sessions，但第 3.2 节排除 Claude subagents 目录，第 4.1 节又说 Claude kind 一律 main。只要求 Claude 跳过消息；Codex subagent/imported 则通过成功 session 查询验证记录存在。
  3. 第 7 节现已明确项目名取首次登记名称，同一 project_id 的笔记路径固定；新增两种 worktree 登记顺序测试。
  4. 第 7 节现已明确 subagent 优先于 imported。重叠测试验证不产生消息且保留会话；第 9 节未要求 session 对外展示 kind，因此不依赖未规定的 JSON 字段或内部表来直接断言 kind 的存储值。
  5. get_project_notes 与 SHA 冲突的 content/sha 在 MCP text 中如何序列化未明确；允许纯文本和内嵌 JSON，不绑定未约定的键名或排版。
  6. 20000 字符存储上限是否包含追加的 …[truncated] 后缀未明确；未对该边界额外作假设。

本套用例覆盖第 12 节全部项目，不宣称穷尽第 3–11 节所有细节。
