# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-16 - [Safeguarding Destructive Actions & Icon Consistency]
**Learning:** Users can accidentally trigger destructive actions like deletion if there's no friction or confirmation. Implementing a confirmation dialog prevents data loss and builds trust. Additionally, using consistent, standard iconography (like Material Symbols) instead of emojis improves the professional feel and predictability of the UI. ARIA labels on icon-only buttons are essential for screen reader users to understand the action.
**Action:** Always require confirmation for destructive actions and use consistent, accessible iconography across the platform.
