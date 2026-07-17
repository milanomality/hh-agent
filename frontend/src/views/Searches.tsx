import { type FormEvent, useState } from "react";
import { api, ApiError, type SearchQuery } from "../api";
import { usePolling } from "../hooks";

export default function Searches({ onAuthError }: { onAuthError: () => void }) {
  const searches = usePolling<SearchQuery[]>(() => api.searches(true), [], onAuthError, 60000);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // общий обработчик мутаций: 401 → на Login, прочие ошибки — показать
  const mutate = async (fn: () => Promise<unknown>, fallback: string): Promise<boolean> => {
    setErr(null);
    try {
      await fn();
      searches.reload();
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        onAuthError();
        return false;
      }
      setErr(e instanceof Error ? e.message : fallback);
      return false;
    }
  };

  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    if (await mutate(() => api.addSearch({ text: text.trim() }), "Не удалось добавить поиск")) {
      setText("");
    }
    setBusy(false);
  };

  const deactivate = (id: number) => mutate(() => api.deactivateSearch(id), "Не удалось выключить поиск");

  const list = searches.data ?? [];

  return (
    <div>
      <form onSubmit={add} className="mb-4 flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="напр. python разработчик"
          className="flex-1 rounded-lg border border-slate-300 bg-transparent px-3 py-2 outline-none focus:border-indigo-500 dark:border-slate-700"
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="rounded-lg bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          Добавить
        </button>
      </form>

      {err && <p className="mb-3 text-sm text-rose-500">{err}</p>}
      {searches.error && <p className="mb-3 text-sm text-rose-500">Ошибка: {searches.error}</p>}
      {list.length === 0 && !searches.loading && (
        <p className="text-slate-400">Поисков пока нет — добавьте первый.</p>
      )}

      <ul className="space-y-2">
        {list.map((s) => (
          <li
            key={s.id}
            className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900"
          >
            <span className={s.active ? "" : "text-slate-400 line-through"}>
              <span className="mr-2">{s.active ? "🟢" : "⚪"}</span>
              <span className="text-slate-400">#{s.id}</span> {s.text}
            </span>
            {s.active && s.id !== null && (
              <button
                onClick={() => deactivate(s.id as number)}
                className="shrink-0 rounded-md px-2 py-1 text-sm text-rose-500 hover:bg-rose-500/10"
              >
                Выключить
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
