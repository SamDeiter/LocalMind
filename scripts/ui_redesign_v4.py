"""
LocalMind UI Redesign — Phase 9 CSS Migration
Applies premium design refresh to style.css:
1. Design token refresh (deeper blacks, larger radii)
2. Sidebar polish (spacing, borders, section headers)
3. Chat message modernization (user bubble gradient, spacing)
4. Input bar premium shape (pill, glow, send button circle)
5. Button refinements (rounder, more tactile)
6. Hardware bar tightening
"""

import shutil

css_path = r"C:\Users\Sam Deiter\Documents\GitHub\LocalMind\frontend\style.css"

# Backup first
shutil.copy2(css_path, css_path + ".bak")
print("[OK] Backed up style.css → style.css.bak")

with open(css_path, "r", encoding="utf-8") as f:
    css = f.read()

changes = 0

def safe_replace(old, new, label):
    global css, changes
    if old in css:
        css = css.replace(old, new)
        changes += 1
        print(f"  [OK] {label}")
    else:
        print(f"  [SKIP] {label} — not found")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. DESIGN TOKENS — deeper blacks, larger radii, warmer accents
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n─── Design Tokens ───")

safe_replace(
    "LocalMind — Design System v3",
    "LocalMind — Design System v4 (Premium)",
    "Version bump"
)

safe_replace(
    "  --bg-primary: #0c0e13;",
    "  --bg-primary: #09090b;",
    "Deeper primary bg"
)
safe_replace(
    "  --bg-secondary: #111319;",
    "  --bg-secondary: #0f1115;",
    "Deeper secondary bg"
)
safe_replace(
    "  --bg-tertiary: #171920;",
    "  --bg-tertiary: #151820;",
    "Deeper tertiary bg"
)
safe_replace(
    "  --bg-surface: rgba(17, 19, 25, 0.85);",
    "  --bg-surface: rgba(15, 17, 21, 0.9);",
    "Denser surface glass"
)
safe_replace(
    "  --bg-glass: rgba(17, 19, 25, 0.6);",
    "  --bg-glass: rgba(12, 14, 18, 0.75);",
    "Denser sidebar glass"
)
safe_replace(
    "  --sidebar-width: 260px;",
    "  --sidebar-width: 280px;",
    "Wider sidebar"
)
safe_replace(
    "  --radius: 12px;",
    "  --radius: 16px;",
    "Larger border radius"
)
safe_replace(
    "  --radius-sm: 8px;",
    "  --radius-sm: 12px;",
    "Larger small radius"
)
safe_replace(
    "  --radius-xs: 6px;",
    "  --radius-xs: 8px;",
    "Larger xs radius"
)

# Add new tokens after transition-slow
safe_replace(
    "  --transition-slow: 0.3s cubic-bezier(0.4, 0, 0.2, 1);\n}",
    """  --transition-slow: 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  --shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.4);
  --shadow-glow: 0 0 20px var(--accent-glow);
  --input-radius: 24px;
}""",
    "New shadow + input tokens"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. SIDEBAR — more breathing room, polished header
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n─── Sidebar ───")

safe_replace(
    "  padding: 16px;\n  border-bottom: 1px solid var(--border);\n}",
    "  padding: 18px 20px;\n  border-bottom: 1px solid var(--border);\n}",
    "Sidebar header padding"
)

safe_replace(
    ".logo-text {\n  font-size: 18px;",
    ".logo-text {\n  font-size: 17px;",
    "Logo text slightly smaller"
)

# Conversation items — more polish
safe_replace(
    ".conversation-item {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  padding: 10px 12px;\n  border-radius: var(--radius-sm);\n  cursor: pointer;\n  transition: background var(--transition);\n  color: var(--text-secondary);\n  font-size: 13.5px;\n}",
    ".conversation-item {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  padding: 11px 14px;\n  border-radius: var(--radius-sm);\n  cursor: pointer;\n  transition: all var(--transition);\n  color: var(--text-secondary);\n  font-size: 13px;\n  font-weight: 400;\n  border-left: 3px solid transparent;\n}",
    "Conversation item polish"
)

safe_replace(
    ".conversation-item.active {\n  background: var(--bg-active);\n  color: var(--text-primary);\n  border-left: 3px solid var(--accent);\n}",
    ".conversation-item.active {\n  background: var(--bg-active);\n  color: var(--text-primary);\n  border-left: 3px solid var(--accent);\n  font-weight: 500;\n}",
    "Active conversation weight"
)

# Section headers — uppercase, pro dashboard look
safe_replace(
    ".sidebar-section-header {\n    padding: 10px 16px;\n    font-size: 0.8rem;\n    color: rgba(255, 255, 255, 0.5);\n    cursor: pointer;\n    display: flex;\n    align-items: center;\n    gap: 6px;\n    transition: color 0.2s;\n}",
    ".sidebar-section-header {\n    padding: 10px 18px;\n    font-size: 0.7rem;\n    font-weight: 600;\n    letter-spacing: 0.06em;\n    text-transform: uppercase;\n    color: rgba(255, 255, 255, 0.4);\n    cursor: pointer;\n    display: flex;\n    align-items: center;\n    gap: 8px;\n    transition: color 0.2s;\n}",
    "Section headers — pro uppercase style"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. CHAT MESSAGES — user bubble gradient, more spacing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n─── Chat Messages ───")

safe_replace(
    ".message {\n  display: flex;\n  gap: 12px;\n  margin-bottom: 24px;",
    ".message {\n  display: flex;\n  gap: 14px;\n  margin-bottom: 28px;",
    "Message spacing increase"
)

# User message bubble — gradient purple tint
safe_replace(
    ".message.user .message-content {\n  background: var(--bg-tertiary);\n  padding: 12px 16px;\n  border-radius: var(--radius) var(--radius) var(--radius) 4px;\n  border: 1px solid var(--border);\n}",
    ".message.user .message-content {\n  background: linear-gradient(135deg, rgba(116, 89, 247, 0.12), rgba(175, 162, 255, 0.08));\n  padding: 14px 18px;\n  border-radius: var(--radius) var(--radius) 4px var(--radius);\n  border: 1px solid rgba(175, 162, 255, 0.15);\n}",
    "User bubble gradient purple"
)

# AI avatar — subtle accent ring
safe_replace(
    ".message.assistant .message-avatar {\n  background: var(--bg-tertiary);\n  border: 1px solid var(--border);\n}",
    ".message.assistant .message-avatar {\n  background: var(--bg-tertiary);\n  border: 1px solid rgba(175, 162, 255, 0.2);\n  box-shadow: 0 0 8px rgba(175, 162, 255, 0.1);\n}",
    "AI avatar accent ring"
)

# Message container padding
safe_replace(
    ".messages-container {\n  flex: 1;\n  overflow-y: auto;\n  padding: 24px;",
    ".messages-container {\n  flex: 1;\n  overflow-y: auto;\n  padding: 28px 32px;",
    "Messages container padding"
)

# Code blocks — deeper bg
safe_replace(
    "  background: #0c0e13;\n  border: 1px solid var(--border);\n  border-radius: var(--radius-sm);\n}",
    "  background: #07080a;\n  border: 1px solid rgba(70, 72, 78, 0.2);\n  border-radius: var(--radius-sm);\n}",
    "Code block deeper bg"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. INPUT BAR — pill shape, premium glow
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n─── Input Bar ───")

safe_replace(
    ".input-container {\n  display: flex;\n  flex-direction: row;\n  align-items: flex-end;\n  gap: 8px;\n  max-width: 800px;\n  margin: 0 auto;\n  background: var(--bg-secondary);\n  border: 1px solid var(--border);\n  border-radius: var(--radius);\n  padding: 8px 12px;",
    ".input-container {\n  display: flex;\n  flex-direction: row;\n  align-items: flex-end;\n  gap: 8px;\n  max-width: 800px;\n  margin: 0 auto;\n  background: var(--bg-secondary);\n  border: 1px solid var(--border);\n  border-radius: var(--input-radius);\n  padding: 10px 16px;",
    "Input pill shape + padding"
)

safe_replace(
    ".input-container:focus-within {\n  border-color: var(--accent);\n  box-shadow: 0 0 0 3px var(--accent-glow);\n}",
    ".input-container:focus-within {\n  border-color: var(--accent);\n  box-shadow: 0 0 0 3px var(--accent-glow), 0 4px 24px rgba(175, 162, 255, 0.08);\n}",
    "Input focus double glow"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. SEND BUTTON — circle, larger, more presence
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n─── Buttons ───")

safe_replace(
    ".btn-send {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  width: 36px;\n  height: 36px;\n  border: none;\n  background: var(--accent-gradient);\n  color: white;\n  border-radius: var(--radius-xs);\n  cursor: pointer;\n  transition: all var(--transition);\n}",
    ".btn-send {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  width: 38px;\n  height: 38px;\n  border: none;\n  background: var(--accent-gradient);\n  color: white;\n  border-radius: 50%;\n  cursor: pointer;\n  transition: all var(--transition);\n  flex-shrink: 0;\n}",
    "Send button circle shape"
)

safe_replace(
    ".btn-send:hover {\n  transform: scale(1.05);\n  box-shadow: 0 2px 12px var(--accent-glow);\n}",
    ".btn-send:hover {\n  transform: scale(1.08);\n  box-shadow: 0 4px 20px var(--accent-glow);\n}",
    "Send button hover effect"
)

# Icon buttons — rounder
safe_replace(
    ".btn-icon {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  width: 36px;\n  height: 36px;\n  border: none;\n  background: transparent;\n  color: var(--text-secondary);\n  border-radius: var(--radius-xs);\n  cursor: pointer;\n  transition: all var(--transition);\n}",
    ".btn-icon {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  width: 36px;\n  height: 36px;\n  border: none;\n  background: transparent;\n  color: var(--text-secondary);\n  border-radius: 50%;\n  cursor: pointer;\n  transition: all var(--transition);\n}",
    "Icon buttons circle shape"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. TOP BAR — gradient bottom border
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n─── Top Bar ───")

safe_replace(
    "  border-bottom: 1px solid var(--border);\n  z-index: 10;\n}",
    "  border-bottom: 1px solid var(--border);\n  border-image: linear-gradient(90deg, transparent, var(--border), transparent) 1;\n  z-index: 10;\n}",
    "Top bar gradient border"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. WELCOME SCREEN — larger title, better cards
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n─── Welcome Screen ───")

safe_replace(
    ".welcome-screen h1 {\n  font-family: var(--font-headline);\n  font-size: 32px;",
    ".welcome-screen h1 {\n  font-family: var(--font-headline);\n  font-size: 36px;",
    "Welcome title larger"
)

safe_replace(
    ".feature-card {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  padding: 14px 18px;\n  background: var(--bg-glass);\n  -webkit-backdrop-filter: blur(12px);\n  backdrop-filter: blur(12px);\n  border: 1px solid var(--border);\n  border-radius: var(--radius);\n  cursor: pointer;\n  transition: all var(--transition);\n  font-size: 14px;\n  color: var(--text-secondary);\n}",
    ".feature-card {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n  padding: 16px 20px;\n  background: var(--bg-glass);\n  -webkit-backdrop-filter: blur(16px);\n  backdrop-filter: blur(16px);\n  border: 1px solid var(--border);\n  border-radius: var(--radius);\n  cursor: pointer;\n  transition: all var(--transition);\n  font-size: 14px;\n  color: var(--text-secondary);\n}",
    "Feature cards larger padding"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. TOOL CALL CARDS — rounder, subtler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n─── Tool Cards ───")

safe_replace(
    ".tool-call-card {\n  margin: 10px 0;\n  border: 1px solid var(--border);\n  border-radius: var(--radius-sm);\n  overflow: hidden;\n  background: var(--bg-secondary);\n}",
    ".tool-call-card {\n  margin: 12px 0;\n  border: 1px solid var(--border);\n  border-radius: var(--radius);\n  overflow: hidden;\n  background: var(--bg-secondary);\n  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);\n}",
    "Tool cards rounder + shadow"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. VERSION BADGE — cleaner
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n─── Version Badge ───")

safe_replace(
    ".version-badge {\n  padding: 6px 12px;\n  font-size: 0.7rem;\n  color: rgba(255, 255, 255, 0.4);\n  text-align: center;\n  letter-spacing: 0.5px;\n  border-top: 1px solid rgba(255, 255, 255, 0.06);\n  cursor: default;\n  transition: color 0.2s;\n}",
    ".version-badge {\n  padding: 8px 18px;\n  font-size: 0.65rem;\n  font-weight: 500;\n  color: rgba(255, 255, 255, 0.3);\n  text-align: center;\n  letter-spacing: 0.08em;\n  text-transform: uppercase;\n  border-top: 1px solid rgba(255, 255, 255, 0.04);\n  cursor: default;\n  transition: color 0.2s;\n}",
    "Version badge cleaner"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 10. MODAL — larger radius, deeper shadow
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n─── Modal ───")

safe_replace(
    ".modal-content {\n  background: var(--bg-secondary);\n  border: 1px solid var(--border);\n  border-radius: var(--radius);\n  padding: 24px;\n  max-width: 500px;\n  width: 90%;\n  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.5);\n}",
    ".modal-content {\n  background: var(--bg-secondary);\n  border: 1px solid var(--border);\n  border-radius: 20px;\n  padding: 28px;\n  max-width: 520px;\n  width: 90%;\n  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);\n}",
    "Modal larger + deeper shadow"
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WRITE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with open(css_path, "w", encoding="utf-8") as f:
    f.write(css)

print(f"\n✅ Applied {changes} design changes to style.css")
print("   Backup saved as style.css.bak")
print("   Hot reload should pick this up automatically if server is running.")
