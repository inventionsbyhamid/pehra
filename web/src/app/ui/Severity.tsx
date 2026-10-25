import type { Severity } from "../../types";
import { SEVERITY_WORDS } from "../words";

// The one accent colour: filled red for critical, a red outline for a warning, grey for info.
export function SeverityTag({ level }: { level: Severity }) {
  return <span className={`sev sev-${level}`}>{SEVERITY_WORDS[level] ?? level}</span>;
}
