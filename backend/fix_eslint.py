import os
import re

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "modules")

def replace_in_file(filepath, pattern, replacement):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Updated {os.path.basename(filepath)}")

# 1. controls.js
controls_path = os.path.join(FRONTEND_DIR, "autonomy", "controls.js")
replace_in_file(controls_path, r"import \{ API, priorityInput, welcomeScreen, chatScreen \} from \"\.\./state\.js\";", r"import { API, priorityInput, chatScreen } from \"../state.js\";")

# 2. status.js
status_path = os.path.join(FRONTEND_DIR, "autonomy", "status.js")
replace_in_file(status_path, r"(\s*)const icon = ACTION_ICONS\[event\.action\] \|\| \"📋\";\n", r"")

# 3. autonomy_ui.js
autonomy_ui_path = os.path.join(FRONTEND_DIR, "autonomy_ui.js")
# Remove global comment
replace_in_file(autonomy_ui_path, r"/\* global Prism, brainPulse, brainDigest, insightContent \*/\n", r"")
# Import brainDigest and insightContent
replace_in_file(autonomy_ui_path, r"import \{ API, priorityInput, chatScreen, welcomeScreen \} from \"\./state\.js\";", r"import { API, priorityInput, chatScreen, welcomeScreen, brainDigest, insightContent } from \"./state.js\";")
# Remove unused icon
replace_in_file(autonomy_ui_path, r"(\s*)const icon = ACTION_ICONS\[event\.action\] \|\| \"📋\";\n", r"")
# Fix Prism.highlight
replace_in_file(autonomy_ui_path, r"Prism\.highlight", r"window.Prism.highlight")
replace_in_file(autonomy_ui_path, r"Prism\.languages", r"window.Prism.languages")

# 4. editor.js
editor_path = os.path.join(FRONTEND_DIR, "editor.js")
replace_in_file(editor_path, r"import \{ \$, escapeHtml, showToast \} from \"\./utils\.js\";", r"import { escapeHtml, showToast } from \"./utils.js\";")

# 5. events.js
events_path = os.path.join(FRONTEND_DIR, "events.js")
replace_in_file(events_path, r"import \{ chatScreen, welcomeScreen \} from \"\./state\.js\";", r"import { chatScreen } from \"./state.js\";")

# 6. media.js
media_path = os.path.join(FRONTEND_DIR, "media.js")
replace_in_file(media_path, r"(\s*)const snapBtn = document\.getElementById\(\"snapshotBtn\"\);\n", r"")

# 7. research_ui.js
research_ui_path = os.path.join(FRONTEND_DIR, "research_ui.js")
replace_in_file(research_ui_path, r"(\s*)const pitch = result\.metadata\?\.pitch \|\| result\.metadata\?\.summary \|\| \"\";\n", r"")
replace_in_file(research_ui_path, r"(\s*)const url = result\.metadata\?\.url \|\| result\.source \|\| \"\";\n", r"")
replace_in_file(research_ui_path, r"(\s*)const published = result\.metadata\?\.published \|\| \"\";\n", r"")

print("Lint fix script completed.")
