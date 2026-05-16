# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-05-16 - [Keyboard Accessibility for Hover-only Actions]
**Learning:** Actions that only appear on hover (like 'Delete' or 'Export' on list items) are invisible to keyboard users. Using Tailwind's `group-focus-within` utility ensures these actions become visible when any element inside the group receives focus, bridging the gap between mouse and keyboard accessibility. Combining this with `aria-label` and `title` attributes on icon-only buttons provides a robust, accessible experience.
**Action:** Use `group-focus-within:opacity-100` for hover-triggered actions and always provide explicit ARIA labels for icon-only buttons.
