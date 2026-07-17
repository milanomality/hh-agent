// Типы ответов API (зеркалят pydantic-модели бэкенда) и тонкий fetch-клиент.

export type Verdict = "apply" | "maybe" | "skip";
export type CardStatus = "new" | "applied" | "skipped";

export interface Employer {
  id: string | null;
  name: string;
}

export interface Card {
  vacancy_id: string;
  search_id: number | null;
  name: string;
  employer: Employer;
  salary_text: string;
  area_name: string;
  url: string;
  published_at: string | null;
  description: string;
  key_skills: string[];
  score: number;
  verdict: Verdict;
  summary: string;
  matches: string[];
  gaps: string[];
  red_flags: string[];
  letter: string;
  status: CardStatus;
  favorite: boolean;
  created_at: string | null;
  applied_at: string | null;
  skipped_at: string | null;
}

export interface CardsPage {
  items: Card[];
  total: number;
}

export interface SearchQuery {
  id: number | null;
  text: string;
  area: string | null;
  salary_from: number | null;
  experience: string | null;
  schedule: string | null;
  active: boolean;
  last_polled_at: string | null;
}

export interface AppEvent {
  id: number | null;
  level: string;
  text: string;
  created_at: string | null;
}

export interface Funnel {
  applications_total: number;
  by_state: Record<string, number>;
  cards_by_status: Record<string, number>;
}

export interface Status {
  last_poll_at: string | null;
  pass_failed: boolean;
  hh_token_present: boolean;
  hh_creds_present: boolean;
}

export interface CardFilters {
  min_score?: number;
  favorite?: boolean;
  status?: CardStatus;
  search_id?: number;
  limit?: number;
  offset?: number;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch("/api" + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (res.status === 401) throw new ApiError(401, "Требуется авторизация");
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* тело не JSON — оставляем statusText */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? "?" + s : "";
}

export const api = {
  session: () => req<{ authenticated: boolean }>("/session"),
  login: (password: string) =>
    req<void>("/login", { method: "POST", body: JSON.stringify({ password }) }),
  logout: () => req<void>("/logout", { method: "POST" }),

  cards: (f: CardFilters) => req<CardsPage>("/cards" + query({ ...f })),
  markApplied: (id: string) => req<Card>(`/cards/${id}/applied`, { method: "POST" }),
  markSkip: (id: string) => req<Card>(`/cards/${id}/skip`, { method: "POST" }),
  setFavorite: (id: string, favorite: boolean) =>
    req<Card>(`/cards/${id}/favorite`, { method: "POST", body: JSON.stringify({ favorite }) }),

  searches: (includeInactive: boolean) =>
    req<SearchQuery[]>("/searches" + query({ include_inactive: includeInactive })),
  addSearch: (body: { text: string; area?: string; salary_from?: number }) =>
    req<SearchQuery>("/searches", { method: "POST", body: JSON.stringify(body) }),
  deactivateSearch: (id: number) => req<void>(`/searches/${id}/deactivate`, { method: "POST" }),

  funnel: () => req<Funnel>("/funnel"),
  events: () => req<AppEvent[]>("/events"),
  status: () => req<Status>("/status"),
};
