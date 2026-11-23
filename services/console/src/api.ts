import { useConsoleStore } from "./store";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface Ticket {
  id: string;
  code: string;
  title: string;
  status: string;
  thread_id: string | null;
  updated_at: number;
}

export interface Assignment {
  ticket_id: string;
  worker_id: string;
  state: string;
  lease_until: number | null;
  result: string | null;
  updated_at: number;
}

export interface Dispatch {
  id: string;
  ticket_id: string;
  node_id: string;
  worker_id: string | null;
  state: string;
  platform?: string;
  attempt: number;
  detail: unknown;
  pr_url?: string;
  pr_base?: string;
  updated_at: number;
}

export interface ThreadRecord {
  id: string;
  origin: string;
  origin_id: string;
  created_at: number;
}

export interface TicketDetail extends Ticket {
  dedup_key: string | null;
  created_at: number;
}

export interface TicketDetailResponse {
  ticket: TicketDetail;
  thread: ThreadRecord | null;
  assignments: Assignment[];
  dispatches: Dispatch[];
  sessions: SessionRecord[];
  test_envs?: Record<string, string>;
}

export interface Signal {
  kind: string;
  id: string;
  ticket_id?: string;
  node_id?: string;
  age_s?: number;
  job?: string;
  day?: string;
  detail?: string | null;
  effect_kind?: string;
  object?: string;
}

export interface NodeRecord {
  id: string;
  name: string;
  host: string;
  platforms: string;
  status: string;
  max_workers: number;
  last_seen: number;
}

export interface Summary {
  events: { total: number; by_verdict: Record<string, number> };
  threads: number;
  tickets: { total: number; by_status: Record<string, number> };
  sessions: number;
  runs: { by_status: Record<string, number> };
}

export interface ControlEvent {
  id: string;
  origin: string;
  origin_id: string;
  thread_id: string | null;
  payload: unknown;
  verdict: string;
  created_at: number;
}

export interface SessionRecord {
  id: string;
  ticket_id: string | null;
  node_id: string | null;
  pinned: number;
  created_at: number;
  updated_at: number;
  turn_count: number;
  last_turn_at?: number | null;
}

export interface TurnRecord {
  session_id: string;
  turn_index: number;
  sha256: string;
  bytes: number;
  head_sha: string | null;
  branch: string | null;
  created_at: number;
  text?: string;
}

export interface SessionTurnsResponse {
  session: SessionRecord;
  turns: TurnRecord[];
}

export interface ThreadDetailResponse {
  thread: ThreadRecord;
  events: ControlEvent[];
  tickets: Ticket[];
}

export interface Topology {
  nodes: NodeRecord[];
  tickets: Ticket[];
  dispatches: Dispatch[];
  sessions: SessionRecord[];
}

export interface TicketList {
  tickets: Ticket[];
}

export interface SignalList {
  signals: Signal[];
}

export interface NodeList {
  nodes: NodeRecord[];
}

export interface EventList {
  events: ControlEvent[];
}

export interface SessionList {
  sessions: SessionRecord[];
}

async function detailOf(response: Response): Promise<string> {
  const fallback = `HTTP ${response.status}`;
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) return detail;
    }
  } catch {
    return fallback;
  }
  return fallback;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    // 身份只来自同站 Cookie（HttpOnly，脚本读不到），不在前端保存任何令牌
    response = await fetch(path, { ...init, headers, cache: "no-store", credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "网络错误：无法连接控制面");
  }

  if (response.status === 401) {
    useConsoleStore().clearConnection();
    throw new ApiError(401, "会话已失效，请重新登录");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await detailOf(response));
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function withQuery(path: string, query: URLSearchParams): string {
  const text = query.toString();
  return text ? `${path}?${text}` : path;
}

export function fetchSummary(): Promise<Summary> {
  return request<Summary>("/internal/summary");
}

export function fetchTickets(params?: { status?: string; limit?: number }): Promise<TicketList> {
  const query = new URLSearchParams();
  if (params?.status) query.set("status", params.status);
  if (params?.limit != null) query.set("limit", String(params.limit));
  return request<TicketList>(withQuery("/internal/tickets", query));
}

export function fetchTicket(id: string): Promise<TicketDetailResponse> {
  return request<TicketDetailResponse>(`/internal/tickets/${encodeURIComponent(id)}`);
}

export function fetchSignals(): Promise<SignalList> {
  return request<SignalList>("/internal/signals");
}

export function fetchNodes(): Promise<NodeList> {
  return request<NodeList>("/internal/nodes");
}

export function fetchEvents(params?: {
  since?: number;
  limit?: number;
  verdict?: string;
  origin?: string;
}): Promise<EventList> {
  const query = new URLSearchParams();
  if (params?.since != null) query.set("since", String(params.since));
  if (params?.limit != null) query.set("limit", String(params.limit));
  if (params?.verdict) query.set("verdict", params.verdict);
  if (params?.origin) query.set("origin", params.origin);
  return request<EventList>(withQuery("/internal/events", query));
}

export function fetchSessions(params?: {
  node_id?: string;
  ticket_id?: string;
  limit?: number;
}): Promise<SessionList> {
  const query = new URLSearchParams();
  if (params?.node_id) query.set("node_id", params.node_id);
  if (params?.ticket_id) query.set("ticket_id", params.ticket_id);
  if (params?.limit != null) query.set("limit", String(params.limit));
  return request<SessionList>(withQuery("/internal/sessions", query));
}

export function fetchSessionTurns(
  id: string,
  includeText = false,
): Promise<SessionTurnsResponse> {
  const query = new URLSearchParams();
  if (includeText) query.set("include_text", "true");
  return request<SessionTurnsResponse>(
    withQuery(`/internal/sessions/${encodeURIComponent(id)}/turns`, query),
  );
}

export function fetchThread(id: string): Promise<ThreadDetailResponse> {
  return request<ThreadDetailResponse>(`/internal/threads/${encodeURIComponent(id)}`);
}

export function fetchTopology(): Promise<Topology> {
  return request<Topology>("/internal/topology");
}

export interface SessionUser {
  sub: string;
  name: string;
  email: string;
  internal: boolean;
  picture: string;
  admin?: boolean;
  role?: string | null;
}

export interface SessionInfo {
  mode: "sso" | "local" | "token";
  provider?: string;
  user?: SessionUser;
  kind?: "admin" | "market";
  permissions?: string[];
}

export interface LoginPayload {
  token?: string;
  email?: string;
  password?: string;
}

export async function probeSession(): Promise<SessionInfo> {
  return request<SessionInfo>("/api/session");
}

export async function loginRequest(payload: LoginPayload): Promise<SessionInfo> {
  return request<SessionInfo>("/api/session/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function logoutRequest(): Promise<void> {
  await request<{ ok: boolean }>("/api/session/logout", { method: "POST" });
}

export interface AssistantRecord {
  id: string;
  name: string;
  purpose: string;
  callback_url: string;
  callback_secret_set: boolean;
  source: string;
  created_by: string;
  created_at: number;
  last_used_at: number | null;
  revoked_at: number | null;
  rotated_at: number | null;
}

export interface IssuedAssistant {
  assistant: AssistantRecord;
  key?: string;
  callback_secret?: string;
}

export function fetchAssistants(): Promise<{ assistants: AssistantRecord[] }> {
  return request<{ assistants: AssistantRecord[] }>("/internal/assistants");
}

export function issueAssistant(payload: {
  name: string;
  purpose?: string;
  callback_url?: string;
}): Promise<IssuedAssistant> {
  return request<IssuedAssistant>("/internal/assistants", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function rotateAssistant(id: string): Promise<IssuedAssistant> {
  return request<IssuedAssistant>(`/internal/assistants/${encodeURIComponent(id)}/rotate`, {
    method: "POST",
  });
}

export function revokeAssistant(id: string): Promise<{ assistant: AssistantRecord }> {
  return request<{ assistant: AssistantRecord }>(`/internal/assistants/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function updateAssistant(
  id: string,
  payload: { purpose?: string; callback_url?: string; new_callback_secret?: boolean },
): Promise<IssuedAssistant> {
  return request<IssuedAssistant>(`/internal/assistants/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export interface LLMCallRecord {
  id: string;
  session_id: string;
  actor: string;
  source: string;
  model: string;
  prompt: string;
  response: string;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
  status: string;
  error: string;
  created_at: number;
}

export interface LLMSessionRecord {
  session_id: string;
  actor: string;
  source: string;
  calls: number;
  tokens: number;
  first_at: number;
  last_at: number;
  model: string;
  last_status: string;
}

export function fetchLLMSessions(limit = 100): Promise<{ sessions: LLMSessionRecord[] }> {
  return request<{ sessions: LLMSessionRecord[] }>(`/internal/llm/sessions?limit=${limit}`);
}

export function fetchLLMCalls(
  sessionId: string,
  limit = 200,
): Promise<{ calls: LLMCallRecord[] }> {
  return request<{ calls: LLMCallRecord[] }>(
    withQuery(
      "/internal/llm/calls",
      new URLSearchParams({ session_id: sessionId, limit: String(limit) }),
    ),
  );
}

export interface AgentMessageRecord {
  role: string;
  content: string;
  created_at?: number;
}

export interface AgentThread {
  thread_id: string;
  actor?: string;
  messages: AgentMessageRecord[];
}

export interface AgentChatResp {
  thread_id: string;
  reply: string;
  tools: string[];
}

export function fetchAgentThread(threadId: string): Promise<AgentThread> {
  return request<AgentThread>(`/internal/agent/thread/${encodeURIComponent(threadId)}`);
}

export function sendAgentMessage(threadId: string, message: string): Promise<AgentChatResp> {
  return request<AgentChatResp>("/internal/agent/chat", {
    method: "POST",
    body: JSON.stringify({ thread_id: threadId, message }),
  });
}
