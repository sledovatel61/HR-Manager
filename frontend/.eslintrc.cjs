module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react-hooks/recommended",
  ],
  parser: "@typescript-eslint/parser",
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },
  plugins: ["@typescript-eslint", "react-refresh"],
  ignorePatterns: ["dist", "node_modules", "coverage"],
  rules: {
    // Приложение не должно экспортировать ничего, кроме компонентов,
    // из hot-модулей; константы-метаданные разрешены.
    "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
  },
};
