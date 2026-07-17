import { api, type AppEvent, type Funnel, type Status } from "../api";
import { usePolling } from "../hooks";

function fmt(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ru-RU");
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="text-sm text-slate-500">{label}</div>
    </div>
  );
}

interface Bundle {
  status: Status;
  funnel: Funnel;
  events: AppEvent[];
}

export default function StatusView({ onAuthError }: { onAuthError: () => void }) {
  const data = usePolling<Bundle>(
    async () => {
      const [status, funnel, events] = await Promise.all([api.status(), api.funnel(), api.events()]);
      return { status, funnel, events };
    },
    [],
    onAuthError,
    30000,
  );

  if (data.error) return <p className="text-sm text-rose-500">Ошибка: {data.error}</p>;
  if (!data.data) return <p className="text-slate-400">Загрузка…</p>;

  const { status, funnel, events } = data.data;

  return (
    <div className="space-y-5">
      {status.pass_failed && (
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-600 dark:text-rose-400">
          ⚠️ Последний проход поиска не удался целиком. Агент повторит попытку по расписанию.
        </div>
      )}
      {!status.hh_creds_present && (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600 dark:text-amber-400">
          Не заданы креды приложения hh (HH_CLIENT_ID / HH_CLIENT_SECRET) — поиск работать не будет.
        </div>
      )}

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Воронка</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Отмечено откликов" value={funnel.applications_total} />
          <Stat label="Новые" value={funnel.cards_by_status.new ?? 0} />
          <Stat label="Отклики" value={funnel.cards_by_status.applied ?? 0} />
          <Stat label="Пропущено" value={funnel.cards_by_status.skipped ?? 0} />
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Состояние</h2>
        <dl className="grid grid-cols-1 gap-2 rounded-xl border border-slate-200 bg-white p-4 text-sm sm:grid-cols-2 dark:border-slate-800 dark:bg-slate-900">
          <div className="flex justify-between gap-4">
            <dt className="text-slate-500">Последний проход</dt>
            <dd>{fmt(status.last_poll_at)}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-slate-500">Токен hh</dt>
            <dd>{status.hh_token_present ? "✅ активен" : "—"}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-slate-500">Креды hh</dt>
            <dd>{status.hh_creds_present ? "✅ заданы" : "❌ нет"}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-slate-500">Проход</dt>
            <dd>{status.pass_failed ? "❌ сбой" : "✅ ок"}</dd>
          </div>
        </dl>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">События</h2>
        {events.length === 0 ? (
          <p className="text-sm text-slate-400">Пока пусто.</p>
        ) : (
          <ul className="space-y-2">
            {events.map((e) => (
              <li
                key={e.id}
                className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm dark:border-slate-800 dark:bg-slate-900"
              >
                <div className="text-xs text-slate-400">{fmt(e.created_at)}</div>
                <div className="whitespace-pre-wrap text-slate-700 dark:text-slate-300">{e.text}</div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
