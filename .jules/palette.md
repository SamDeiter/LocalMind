# Palette's Journal - UX & Accessibility Learnings

## 2025-05-15 - [Enhanced Voice Feedback & Typing Visibility]
**Learning:** Interactive elements like voice input and typing indicators are crucial for user experience but often lack clear visual feedback or accessibility considerations. A pulsing animation for the active microphone provides immediate, non-intrusive feedback, while dynamic `aria-label` updates ensure accessibility for screen readers. Consistent styling for typing indicators across the application prevents fragmented user experiences.
**Action:** Always ensure interactive states have distinct visual cues and corresponding accessibility attributes to maintain a delightful and inclusive interface.

## 2025-10-24 - [Accessible Conversation Actions & Safety Safeguards]
**Learning:** System-critical list items (such as chat history) that have adjacent interactive buttons require solid safety safeguards like confirmation prompts (`window.confirm`) to prevent accidental, frustrating data loss. Replacing raw emojis with CDN-loaded Material Symbols combined with dark-theme Tailwind classes (`text-indigo-300`, `border-primary`) improves cohesive aesthetics across operating systems. Additionally, including keyboard-accessible ring outlines (`focus-visible:ring-2`) and descriptive `aria-label` tags ensures accessibility for screen readers and keyboard-only users.
**Action:** Always pair high-quality Material icons with robust accessibility attributes, keyboard-focusable states, and safety validation prompts for destructive user actions.
