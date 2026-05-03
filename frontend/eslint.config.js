import globals from "globals";
import js from "@eslint/js";

export default [
  js.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        // CDN globals loaded via <script> tags
        marked: "readonly",
        hljs: "readonly",
        monaco: "readonly",
        require: "readonly",
      },
    },
    rules: {
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "no-console": "off",
      "prefer-const": "error",
      "no-var": "error",
      // Allow `x == null` (and `!= null`) — the standard "null OR undefined" idiom.
      // Strict equality required everywhere else.
      eqeqeq: ["error", "always", { "null": "ignore" }],
      // Empty catch (_) {} is the codebase's intentional "swallow this" idiom.
      "no-empty": ["error", { "allowEmptyCatch": true }],
    },
  },
  {
    files: ["__tests__/**/*.js"],
    languageOptions: {
      globals: {
        ...globals.jest,
      },
    },
  },
];
