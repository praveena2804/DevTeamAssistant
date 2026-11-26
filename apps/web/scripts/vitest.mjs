// Runs Vitest from this package with a normalised working directory (KI-048).
//
// On Windows, starting Vitest from a working directory spelled with a lower-case drive letter ("c:\...")
// makes it load its runner under two spellings of the same path, and every file fails with "Vitest failed
// to find the current suite". Vitest is therefore started as a child process whose working directory is
// this package with an upper-case drive letter. Arguments are passed through unchanged.
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(dirname(fileURLToPath(import.meta.url)));
const cwd = here.replace(/^[a-z]:/, (drive) => drive.toUpperCase());
const cli = join(cwd, "node_modules", "vitest", "vitest.mjs");
const result = spawnSync(process.execPath, [cli, ...process.argv.slice(2)], { cwd, stdio: "inherit" });
process.exit(result.status ?? 1);
