import { useMemo, useState } from "react";
import { api, type CardsPage, type CardStatus, type SearchQuery } from "../api";
import { usePolling } from "../hooks";
import CardView from "../components/Card";

const STATUS_TABS: { key: CardStatus | "all"; label: string }[] = [
  { key: "new", label: "Новые" },
  { key: "applied", label: "Отклики" },
  { key: "skipped", label: "Пропущенные" },
  { key: "all", label: "Все" },
];

export default function Feed({ onAuthError }: { onAuthError: () => void }) {
  const [status, setStatus] = useState<CardStatus | "all">("new");
  const [minScore, setMinScore] = useState(0);
  const [favOnly, setFavOnly] = useState(false);
  const [searchId, setSearchId] = useState<number | "">("");

  const searches = usePolling<SearchQuery[]>(() => api.searches(true), [], onAuthError, 60000);

  const filters = useMemo(
    () => ({
      status: status === "all" ? undefined : status,
      min_score: minScore || undefined,
      favorite: favOnly || undefined,
      search_id: typeof searchId === "number" ? searchId : undefined,
      limit: 100,
    }),
    [status, minScore, favOnly, searchId],
  );

  const feed = usePolling<CardsPage>(() => api.cards(filters), [filters], onAuthError, 30000);
  const cards = feed.data?.items ?? [];

  return (
    <div>
      <div className="mb-4 space-y-3 rounded-2xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-wrap gap-1">
          {STATUS_TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setStatus(t.key)}
              className={
                "rounded-full px-3 py-1 text-sm font-medium transition " +
                (status === t.key
                  ? "bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300")
              }
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <label className="flex items-center gap-2">
            Оценка ≥ <span className="font-semibold tabular-nums">{minScore}</span>
            <input
              type="range"
              min={0}
              max={10}
              value={minScore}
              onChange={(e) => setMinScore(Number(e.target.value))}
              className="accent-indigo-600"
            />
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={favOnly} onChange={(e) => setFavOnly(e.target.checked)} className="accent-amber-500" />
            ⭐ избранное
          </label>
          <select
            value={searchId}
            onChange={(e) => setSearchId(e.target.value ? Number(e.target.value) : "")}
            className="rounded-md border border-slate-300 bg-transparent px-2 py-1 dark:border-slate-700"
          >
            <option value="">все поиски</option>
            {(searches.data ?? []).map((s) => (
              <option key={s.id} value={s.id ?? ""}>
                #{s.id} {s.text}
              </option>
            ))}
          </select>
        </div>
      </div>

      {feed.error && <p className="mb-3 text-sm text-rose-500">Ошибка загрузки: {feed.error}</p>}
      {feed.loading && cards.length === 0 && <p className="text-slate-400">Загрузка…</p>}
      {!feed.loading && cards.length === 0 && (
        <p className="rounded-2xl border border-dashed border-slate-300 p-8 text-center text-slate-400 dark:border-slate-700">
          Пока пусто. Карточки появятся после ближайшего прохода поиска.
        </p>
      )}

      <div className="space-y-3">
        {cards.map((c) => (
          <CardView key={c.vacancy_id} card={c} onChanged={feed.reload} onAuthError={onAuthError} />
        ))}
      </div>
    </div>
  );
}
