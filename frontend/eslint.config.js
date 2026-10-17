import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["dist/**", "node_modules/**", "coverage/**"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-non-null-assertion": "error",
      ...reactHooks.configs.recommended.rules,
    }
  },
  {
    files: ["scripts/**/*.mjs", "vite.config.ts"],
    languageOptions: {
      globals: {
        process: "readonly",
      },
    },
  },
);
