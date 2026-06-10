# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-22 - [Sidebar Action Accessibility & Discoverability]
**Learning:** Hidden action buttons in list items (like 'Delete' or 'Export') are often inaccessible to keyboard users and screen readers if they only appear on hover. Using `group-focus-within` ensures they become visible when any element in the item is focused. Replacing emojis with semantic Material Symbols and adding explicit ARIA labels improves professional aesthetic and screen reader clarity.
**Action:** Always use `group-focus-within` for hover-triggered actions and prefer semantic icons with ARIA labels over emojis.
