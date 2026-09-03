import { Icon, type IconName } from "../icons/Icon";
import type { CandidateStage, StageTone } from "../../types";
import { STAGE_LABELS, STAGE_TONE } from "../../types";
import "./statusChip.css";

/**
 * Статус кандидата НИКОГДА не передаётся только цветом: у каждого тона есть
 * своя иконка-глиф, так что чип различим даже в оттенках серого /
 * при дальтонизме (WCAG 1.4.1 "Use of Color").
 */
const TONE_ICON: Record<StageTone, IconName> = {
  neutral: "clock",
  info: "phone",
  teal: "check-circle",
  violet: "calendar",
  indigo: "check-circle",
  amber: "star",
  success: "check",
  danger: "close",
};

export function StageChip({ stage, size = "md" }: { stage: CandidateStage; size?: "sm" | "md" }) {
  const tone = STAGE_TONE[stage];
  return (
    <span className={`status-chip status-chip-${tone} status-chip-${size}`}>
      <Icon name={TONE_ICON[tone]} size={size === "sm" ? 11 : 12} />
      {STAGE_LABELS[stage]}
    </span>
  );
}

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: StageTone;
  children: React.ReactNode;
}) {
  return <span className={`status-chip status-chip-${tone} status-chip-sm badge-plain`}>{children}</span>;
}
