import { Badge, type Tone } from "@/components/ui";
import type { Summary, Verification, VersionStatus } from "@/lib/types";

const DOC: Record<VersionStatus, [Tone, string]> = {
  uploaded: ["neutral", "Uploaded"], queued: ["info", "Queued"], processing: ["info", "Processing"],
  ready: ["ok", "Ready"], failed: ["bad", "Failed"],
};
export const DocStatus = ({ status }: { status: VersionStatus }) => <Badge tone={DOC[status][0]}>{DOC[status][1]}</Badge>;

const VER: Record<Verification, [Tone, string]> = {
  verified: ["ok", "Verified"], partial: ["warn", "Partially supported"], unsupported: ["bad", "Unsupported"],
  unverified: ["neutral", "Unverified"],
};
export const VerificationBadge = ({ v }: { v: Verification }) => <Badge tone={VER[v][0]}>{VER[v][1]}</Badge>;

const SUM: Record<Summary, [Tone, string]> = {
  verified: ["ok", "All claims verified"], partially_verified: ["warn", "Partially verified"],
  insufficient_evidence: ["bad", "Insufficient evidence"],
};
export const SummaryBadge = ({ s }: { s: Summary }) => <Badge tone={SUM[s][0]}>{SUM[s][1]}</Badge>;
