import type { CandidateStage } from "../../types";
export interface DocumentItem {
  key: string;
  name: string;
  explanation: string;
  required: boolean;
}
export interface DocumentVersion {
  id: string;
  list_id: string;
  number: number;
  stage: string;
  state: "draft" | "published" | "archived";
  name: string;
  description: string;
  items: DocumentItem[];
  author_id: string;
  created_at: string;
  published_at: string | null;
}
export interface DocumentList {
  id: string;
  stage: string;
  version: number;
  versions: DocumentVersion[];
}
export interface DocumentLists {
  items: DocumentList[];
  can_manage: boolean;
}
export interface VersionInput {
  name: string;
  description: string;
  items: DocumentItem[];
}
export interface CandidateDocuments {
  revision: number;
  set_id: string | null;
  exact_version: DocumentVersion | null;
  items: (DocumentItem & {
    state: "missing" | "received";
    version: number;
    changed_by: string;
    changed_at: string;
  })[];
  missing_required: string[];
}
export interface RuleParams {
  trigger: "stage_transition" | "scheduled_reminder";
  action: "apply_list" | "document_request" | "document_reminder";
  stage: CandidateStage;
  list_id: string;
  list_version_id: string | null;
  missing_required: boolean;
  channel: "email" | "telegram" | null;
  days: number | null;
}
export interface RuleInput {
  name: string;
  enabled: boolean;
  params: RuleParams;
}
export interface DocumentRule extends RuleInput {
  id: string;
  version: number;
  created_at: string;
  updated_at: string;
}
export interface RuleExecution {
  id: string;
  rule_version: number;
  trigger_id: string;
  trigger_version: number;
  candidate_id: string;
  action: string;
  outcome: string;
  created_at: string;
  outbox_id: string;
}
