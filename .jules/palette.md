# 🎨 Palette's Journal - LocalMind

## 2026-05-15 - Accessibility & Safety Guards
**Learning:** Icon-only buttons without tooltips or ARIA labels make the interface less accessible and harder to navigate. Adding simple confirmation dialogs to destructive actions (like clearing a session) prevents user frustration from accidental clicks.
**Action:** Always ensure icon-only buttons have descriptive `title` and `aria-label` attributes. Implement confirmation guards for non-reversible state changes.
