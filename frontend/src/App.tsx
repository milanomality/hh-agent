import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import Login from "./views/Login";
import Feed from "./views/Feed";
import Searches from "./views/Searches";
import StatusView from "./views/Status";

type Tab = "feed" | "searches" | "status";

const TABS: { key: Tab; label: string }[] = [
  { key: "feed", label: "Вакансии" },
  { key: "searches", label: "Поиски" },
  { key: "status", label: "Статус" },
];

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [tab, setTab] = useState<Tab>("feed");

  useEffect(() => {
    api
      .session()
      .then((s) => setAuthed(s.authenticated))
      .catch(() => setAuthed(false));
  }, []);

  const onAuthError = useCallback(() => setAuthed(false), []);

  const logout = async () => {
    try {
      await api.logout();
    } catch {
      /* всё равно выходим локально */
    }
    setAuthed(false);
  };

  if (authed === null) {
    return <div className="grid min-h-screen place-items-center text-slate-400">Загрузка…</div>;
  }
  if (!authed) return <Login onLogin={() => setAuthed(true)} />;

  return (
    <div className="mx-auto min-h-screen max-w-3xl px-3 pb-16 sm:px-4">
      <header className="sticky top-0 z-10 -mx-3 mb-4 border-b border-slate-200 bg-slate-100/90 px-3 py-3 backdrop-blur sm:-mx-4 sm:px-4 dark:border-slate-800 dark:bg-slate-950/90">
        <div className="flex items-center justify-between gap-2">
          <h1 className="text-lg font-semibold tracking-tight">hh-agent</h1>
          <button
            onClick={logout}
            className="rounded-md px-2 py-1 text-sm text-slate-500 hover:bg-slate-200 dark:hover:bg-slate-800"
          >
            Выйти
          </button>
        </div>
        <nav className="mt-3 flex gap-1">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={
                "rounded-md px-3 py-1.5 text-sm font-medium transition " +
                (tab === t.key
                  ? "bg-indigo-600 text-white"
                  : "text-slate-600 hover:bg-slate-200 dark:text-slate-300 dark:hover:bg-slate-800")
              }
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <main>
        {tab === "feed" && <Feed onAuthError={onAuthError} />}
        {tab === "searches" && <Searches onAuthError={onAuthError} />}
        {tab === "status" && <StatusView onAuthError={onAuthError} />}
      </main>
    </div>
  );
}
