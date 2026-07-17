import { useState } from "react";
import { api, ApiError, type Card, type Verdict } from "../api";

const VERDICT: Record<Verdict, { label: string; cls: string }> = {
  apply: { label: "стоит откликнуться", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  maybe: { label: "может подойти", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  skip: { label: "лучше пропустить", cls: "bg-rose-500/15 text-rose-600 dark:text-rose-400" },
};

const STATUS_BADGE: Record<string, string> = {
  applied: "✅ Отклик отмечен",
  skipped: "⏭ Пропущено",
};

function Bullets({ items, marker }: { items: string[]; marker: string }) {
  if (items.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5 text-sm">
      {items.map((it, i) => (
        <li key={i} className="flex gap-1.5">
          <span aria-hidden>{marker}</span>
          <span className="text-slate-700 dark:text-slate-300">{it}</span>
        </li>
      ))}
    </ul>
  );
}

export default function CardView({
  card,
  onChanged,
  onAuthError,
}: {
  card: Card;
  onChanged: () => void;
  onAuthError: () => void;
}) {
  const [showLetter, setShowLetter] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        onAuthError();
        return;
      }
      setErr(e instanceof Error ? e.message : "Не удалось выполнить действие");
    } finally {
      setBusy(false);
    }
  };

  const v = VERDICT[card.verdict];

  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-3">
        <h2 className="font-semibold leading-snug">
          {card.url ? (
            <a href={card.url} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline dark:text-indigo-400">
              {card.name}
            </a>
          ) : (
            card.name
          )}
        </h2>
        <span className="shrink-0 rounded-lg bg-slate-100 px-2 py-1 text-sm font-semibold tabular-nums dark:bg-slate-800">
          {card.score}/10
        </span>
      </div>

      <div className="mt-1 text-sm text-slate-500">
        {card.employer.name && <span>🏢 {card.employer.name}</span>}
      </div>
      <div className="mt-0.5 text-sm text-slate-500">
        💰 {card.salary_text}
        {card.area_name && <span> · 📍 {card.area_name}</span>}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className={"rounded-full px-2 py-0.5 text-xs font-medium " + v.cls}>{v.label}</span>
        {card.favorite && <span className="text-xs text-amber-500">⭐ в избранном</span>}
        {STATUS_BADGE[card.status] && (
          <span className="text-xs font-medium text-slate-500">{STATUS_BADGE[card.status]}</span>
        )}
      </div>

      {card.summary && <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">{card.summary}</p>}

      <Bullets items={card.matches} marker="✅" />
      <Bullets items={card.gaps} marker="⚠️" />
      <Bullets items={card.red_flags} marker="🚩" />

      {card.letter && (
        <div className="mt-3">
          <button
            onClick={() => setShowLetter((s) => !s)}
            className="text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400"
          >
            ✉️ {showLetter ? "Скрыть письмо" : "Сопроводительное письмо"}
          </button>
          {showLetter && (
            <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-sm text-slate-700 dark:bg-slate-800 dark:text-slate-300">
              {card.letter}
            </pre>
          )}
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {card.url && (
          <a
            href={card.url}
            target="_blank"
            rel="noreferrer"
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500"
          >
            🔗 Открыть на hh.ru
          </a>
        )}
        <button
          disabled={busy || card.status === "applied"}
          onClick={() => act(() => api.markApplied(card.vacancy_id))}
          className="rounded-lg border border-emerald-500/40 px-3 py-1.5 text-sm font-medium text-emerald-600 hover:bg-emerald-500/10 disabled:opacity-40 dark:text-emerald-400"
        >
          ✅ Откликнулся
        </button>
        <button
          disabled={busy || card.status === "skipped"}
          onClick={() => act(() => api.markSkip(card.vacancy_id))}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          ⏭ Пропустить
        </button>
        <button
          disabled={busy}
          onClick={() => act(() => api.setFavorite(card.vacancy_id, !card.favorite))}
          className="rounded-lg border border-amber-500/40 px-3 py-1.5 text-sm font-medium text-amber-600 hover:bg-amber-500/10 disabled:opacity-40 dark:text-amber-400"
        >
          {card.favorite ? "★ В избранном" : "⭐ В избранное"}
        </button>
      </div>
      {err && <p className="mt-2 text-sm text-rose-500">{err}</p>}
    </article>
  );
}
