import sys

filepath = "frontend/modules/state.js"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

new_content = content.replace("apiOrigin = `http://${window.location.hostname}:8000`;", "apiOrigin = `http://${window.location.hostname}:8001`;")

if new_content == content:
    print("No changes made.")
    sys.exit(1)

with open(filepath, "w", encoding="utf-8") as f:
    f.write(new_content)
print("Fix applied to state.js successfully.")
