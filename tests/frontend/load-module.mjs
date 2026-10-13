// Load a dependency-free repo ES module without a package.json by importing its
// source through a data: URL (per the approved plan's Node test approach).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
export const repoRoot = path.resolve(here, "..", "..");

export async function loadModule(rel) {
  const src = readFileSync(path.join(repoRoot, rel), "utf8");
  const b64 = Buffer.from(src, "utf8").toString("base64");
  return import(`data:text/javascript;base64,${b64}`);
}
