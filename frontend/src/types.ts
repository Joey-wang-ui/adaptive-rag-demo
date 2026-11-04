export type Strategy = "direct" | "single" | "multi" | "agent" | "refuse";

export interface Citation {
  document_id: string;
  title: string;
  chunk_id: string;
  section: string | null;
}

export interface ActionProposal {
  action_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  risk: string;
  summary: string;
}

export interface ChatResponse {
  answer: string;
  session_id: string;
  trace_id: string;
  intent: string | null;
  complexity: string | null;
  strategy: Strategy | null;
  citations: Citation[];
  pending_action: ActionProposal | null;
  refused: boolean;
  error_code: string | null;
  latency: { total_ms: number };
}

export interface ChatRequest {
  message: string;
  session_id?: string;
  user_id: string;
  confirm_action_id?: string;
}

export type StreamEvent =
  | { event: "started"; trace_id: string }
  | { event: "node"; node: string; intent?: string; strategy?: Strategy }
  | { event: "token"; text: string }
  | { event: "answer"; data: ChatResponse }
  | { event: "done"; trace_id: string };

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  state: "complete" | "streaming" | "error" | "stopped";
  citations?: Citation[];
  pendingAction?: ActionProposal | null;
  strategy?: Strategy | null;
  traceId?: string;
  latencyMs?: number;
}

export interface Conversation {
  id: string;
  title: string;
  updatedAt: number;
  sessionId?: string;
  messages: Message[];
}

export interface BusinessProfile {
  mode: "jiahui" | "ecommerce";
  name: string;
  welcome: string;
  examples: string[];
  product_count: number;
  reference_product_count?: number;
  current_product_count?: number;
  catalog_status: string;
  knowledge_document_count?: number;
  knowledge_file_count?: number;
  reference_document_count?: number;
  capabilities: { llm: string; embedding: string; reranker: string };
}

export interface Principal {
  username: string;
  role: string;
  scopes: string[];
}

export interface DesktopStatus {
  version?: string;
  desktop: boolean;
  setup_required: boolean;
  secure_storage: boolean;
}
export interface DesktopSettings {
  data_dir: string;
  api_key_configured: boolean;
  secure_storage: boolean;
  capabilities: BusinessProfile["capabilities"];
  current_products: number;
  reference_products?: number;
  product_count?: number;
  draft_products: number;
  knowledge_document_count?: number;
  knowledge_file_count?: number;
  reference_document_count?: number;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  source_file: string;
  location: string;
  version: string;
  status: string;
}
export interface ImportResult {
  imported?: number;
  total?: number;
  pages?: number;
  imported_pages?: number;
  catalog_imported?: number;
  reference_products?: number;
  catalog_changed?: boolean;
  skipped_pages?: { page: number; reason: string }[];
  message?: string;
}
