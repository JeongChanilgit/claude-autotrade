import { useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { getHealth } from "../api";

interface LayoutProps {
  children: React.ReactNode;
}

const navItems = [
  { to: "/", label: "Home", end: true },
  { to: "/run/new", label: "New Run" },
  { to: "/compare", label: "Compare" },
  { to: "/gate-reports", label: "Gate Reports" },
];

export default function Layout({ children }: LayoutProps) {
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    getHealth()
      .then((h) => setVersion(h.version))
      .catch(() => setVersion(null));
  }, []);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex">
      <aside className="w-56 border-r border-slate-800 bg-slate-900 p-4 flex flex-col gap-2">
        <Link to="/" className="text-xl font-bold tracking-tight mb-4">
          Kairos
        </Link>
        <nav className="flex flex-col gap-1">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                [
                  "px-3 py-2 rounded text-sm",
                  isActive
                    ? "bg-slate-800 text-white"
                    : "text-slate-400 hover:text-white hover:bg-slate-800",
                ].join(" ")
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex-1 flex flex-col">
        <header className="h-12 border-b border-slate-800 bg-slate-900 px-6 flex items-center justify-between">
          <div className="text-sm text-slate-400">
            OOS backtest-only validation
          </div>
          <div className="text-xs px-2 py-1 rounded bg-slate-800 text-emerald-400 font-mono">
            v{version ?? "…"}
          </div>
        </header>
        <main className="flex-1 p-6 overflow-auto">{children}</main>
      </div>
    </div>
  );
}
