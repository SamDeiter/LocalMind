export const MAX_ACTIVITY_ITEMS = 15;

/** Module-scoped brain state (replaces window.* globals). */
export const brainState = {
  proposalCount: 0,
  executedCount: 0,
  bootTime: null,
  caughtUp: false,
};

export const ACTION_ICONS = {
  idle: "💤",
  thinking: "🧠",
  reflecting: "🔍",
  proposal_created: "💡",
  auto_approved: "🤖",
  checking: "🔎",
  executing: "⚡",
  git: "🌿",
  writing: "✍️",
  testing: "🧪",
  completed: "✅",
  merged: "🔀",
  reverted: "⚠️",
  error: "❌",
  mode_changed: "🔄",
};
