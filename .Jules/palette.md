
## 2025-05-15 - Improving Sidebar Accessibility and Safety
**Learning:** Icon-only buttons in dense lists (like conversation sidebars) need both visual consistency via Material Symbols and technical accessibility via aria-labels. Destructive actions must always be guarded by a confirmation dialog to prevent accidental data loss.
**Action:** Always include aria-label for icon-only buttons and wrap destructive API calls in window.confirm() when working with list-item actions.
