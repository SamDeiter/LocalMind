import os

file_path = r'c:\Users\Sam Deiter\Documents\GitHub\LocalMind\backend\routes\chat.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Indentified stray lines (based on view_file):
# 202: async def chat(request: Request):
# 203:     message = await parse_message(request)
# 204:     response = await generate_response(message)
# 205:     return handle_response(response)
# 206:     """Main chat endpoint
#
# We need to remove lines 203, 204, 205 (indices 202, 203, 204)

new_lines = []
for i, line in enumerate(lines):
    # Check for the specific pattern to be safe
    if i == 202 and 'message = await parse_message(request)' in line:
        continue
    if i == 203 and 'response = await generate_response(message)' in line:
        continue
    if i == 204 and 'return handle_response(response)' in line:
        continue
    new_lines.append(line)

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"Removed 3 stray lines from {file_path}")
