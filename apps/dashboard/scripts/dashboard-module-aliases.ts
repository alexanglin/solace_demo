import { fileURLToPath } from "node:url";

export const dashboardModuleAliases = {
  "ajv/dist/runtime/ucs2length.js": fileURLToPath(
    new URL("../src/contracts/generated/runtime/ucs2length-runtime.mjs", import.meta.url),
  ),
} as const;
