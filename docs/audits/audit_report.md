# AUDIT REPORT

## Long Files (>300 lines)
- backend\autonomy.py: 1256 lines
- backend\research_engine.py: 1084 lines
- frontend\modules\autonomy_ui.js: 915 lines
- backend\server.py: 575 lines
- frontend\index.html: 567 lines
- backend\proposals.py: 548 lines
- backend\code_editor.py: 538 lines
- tests\test_autonomy.py: 478 lines
- frontend\stitch_export.html: 469 lines
- frontend\modules\chat.js: 463 lines
- backend\self_improver.py: 453 lines
- backend\tools.py: 405 lines
- frontend\modules\editor.js: 367 lines
- backend\tools\git_tools.py: 355 lines
- scripts\sprint1_rag_apply.py: 353 lines
- backend\routes\autonomy_routes.py: 333 lines
- backend\metacognition\controller.py: 317 lines
- frontend\modules\research_ui.js: 306 lines
- backend\routes\research_routes.py: 303 lines

## Long Functions (>100 lines)
- backend\proposals.py -> `_normalize_title`: 471 lines (Starting line 78)
- tests\test_autonomy.py -> `sample_proposal`: 441 lines (Starting line 38)
- backend\code_editor.py -> `is_protected_file`: 327 lines (Starting line 111)
- backend\tools\git_tools.py -> `_run_git`: 313 lines (Starting line 43)
- backend\routes\autonomy_routes.py -> `configure`: 301 lines (Starting line 33)
- backend\server.py -> `_configure_routers`: 284 lines (Starting line 292)
- scripts\ui_redesign_v4.py -> `safe_replace`: 264 lines (Starting line 25)
- frontend\modules\chat.js -> `sendMessage`: 244 lines (Starting line 72)
- backend\tools\self_edit.py -> `_validate_self_path`: 231 lines (Starting line 34)
- tests\test_server.py -> `client`: 223 lines (Starting line 45)
- backend\tools\dependency_manager.py -> `pin_dependency`: 213 lines (Starting line 69)
- frontend\modules\autonomy_ui.js -> `renderTaskPipeline`: 210 lines (Starting line 629)
- backend\tools\mcp_browser.py -> `_extract_title`: 204 lines (Starting line 75)
- backend\tools.py -> `web_search`: 181 lines (Starting line 211)
- frontend\modules\research_ui.js -> `searchArxiv`: 175 lines (Starting line 20)
- tests\test_git_tools.py -> `temp_project_dir`: 171 lines (Starting line 40)
- backend\tools\memory.py -> `get_recent_memories`: 168 lines (Starting line 67)
- backend\tools\file_tools.py -> `_validate_path`: 165 lines (Starting line 15)
- tests\test_research.py -> `client`: 159 lines (Starting line 13)
- backend\routes\conversations.py -> `configure`: 156 lines (Starting line 35)
- frontend\modules\autonomy_ui.js -> `pollAutonomy`: 152 lines (Starting line 13)
- frontend\modules\events.js -> `bindEvents`: 140 lines (Starting line 34)
- backend\tools\project_context.py -> `_build_tree`: 138 lines (Starting line 57)
- frontend\modules\proposals_ui.js -> `timeAgo`: 138 lines (Starting line 66)
- frontend\modules\editor.js -> `onload`: 136 lines (Starting line 232)
- backend\routes\research_routes.py -> `_get_file_list`: 135 lines (Starting line 169)
- backend\tools\propose_action.py -> `get_pending_requests`: 134 lines (Starting line 65)
- backend\server.py -> `estimate_task_complexity`: 129 lines (Starting line 143)
- tests\test_dependency_manager.py -> `tmp_deps_file`: 120 lines (Starting line 25)
- run.py -> `main`: 111 lines (Starting line 135)
- backend\tools\run_code.py -> `_safety_check`: 104 lines (Starting line 38)

## Potential Secrets/Hardcoded Credentials

## Hardcoded Localhost References
- backend\tools\vision.py
- backend\metacognition\intent_parser.py
- backend\config.py
- backend\server.py
- backend\autonomy.py
- backend\metacognition\self_checker.py
- backend\routes\research_routes.py
- backend\tools\manage_model.py
- backend\tools\memory.py
- backend\metacognition\revision_controller.py
- tests\test_autonomy.py
- run.py
- backend\agent.py
- backend\metacognition\controller.py
