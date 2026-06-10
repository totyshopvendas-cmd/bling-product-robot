import { useState, useEffect, useCallback } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  LayoutDashboard, Sparkles, Tags, Bot, ScrollText, Settings as SettingsIcon,
  CircleDot, Power, Menu, X, Layers, ListChecks, Share2, Megaphone, CalendarClock, Rocket, Activity as ActivityIcon,
} from "lucide-react";
import { endpoints } from "@/lib/api";
import { logger } from "@/lib/logger";

const NAV = [
  { to: "/", icon: LayoutDashboard, label: "Painel" },
  { to: "/limpeza", icon: Sparkles, label: "Limpeza de Título" },
  { to: "/precos", icon: Tags, label: "Tabela de Preços" },
  { to: "/robo", icon: Bot, label: "Robô JohnDrop" },
  { to: "/bling", icon: Layers, label: "Enriquecimento Bling" },
  { to: "/bling-lote", icon: ListChecks, label: "Enriquecer em Lote" },
  { to: "/progresso", icon: ActivityIcon, label: "Progresso" },
  { to: "/redes-sociais", icon: Share2, label: "Redes Sociais" },
  { to: "/setup-redes", icon: Rocket, label: "Setup Wizard" },
  { to: "/criar-anuncio", icon: Megaphone, label: "Criar Anúncio" },
  { to: "/agenda", icon: CalendarClock, label: "Agenda" },
  { to: "/logs", icon: ScrollText, label: "Logs" },
  { to: "/configuracoes", icon: SettingsIcon, label: "Configurações" },
];

const STATUS_POLL_INTERVAL_MS = 4000;

const STATE_LABEL = {
  idle: { label: "Ocioso", color: "bg-zinc-400", dotColor: "bg-zinc-400" },
  running: { label: "Em execução", color: "bg-emerald-500", dotColor: "bg-emerald-500" },
  paused: { label: "Pausado", color: "bg-amber-500", dotColor: "bg-amber-500" },
  error: { label: "Erro", color: "bg-rose-500", dotColor: "bg-rose-500" },
};

export default function Layout({ children }) {
  const location = useLocation();
  const [robotState, setRobotState] = useState("idle");
  const [open, setOpen] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const { data } = await endpoints.robotStatus();
      setRobotState(data.state);
    } catch (err) {
      logger.error("Failed to fetch robot status:", err);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const timer = setInterval(fetchStatus, 4000);
    return () => clearInterval(timer);
  }, [fetchStatus]);
  const meta = STATE_LABEL[robotState] || STATE_LABEL.idle;

  return (
    <div className="min-h-screen flex bg-background text-foreground">
      {/* Sidebar */}
      <aside
        data-testid="app-sidebar"
        className={`${open ? "fixed inset-y-0 left-0 z-40" : "hidden"} md:flex md:static w-64 flex-col border-r border-border bg-white`}
      >
        <div className="flex items-center gap-3 px-5 py-4 border-b border-border">
          <img
            src="https://customer-assets.emergentagent.com/job_bling-product-robot/artifacts/r2hhfsdn_TotyShop%20%201080x1080.jpg"
            alt="TotyShop"
            className="h-11 w-11 rounded-sm object-cover"
            data-testid="brand-logo"
          />
          <div>
            <div className="font-display font-bold tracking-tight text-base leading-none">
              <span className="text-zinc-900">Toty</span><span className="text-[#EE7B22]">Shop</span>
            </div>
            <div className="label-overline mt-1">Automação</div>
          </div>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          {NAV.map(({ to, icon: Icon, label }) => {
            const active = location.pathname === to;
            return (
              <Link
                key={to}
                to={to}
                data-testid={`nav-${to.replace("/", "") || "home"}`}
                onClick={() => setOpen(false)}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-sm text-sm transition-colors ${
                  active
                    ? "bg-[#EE7B22] text-white"
                    : "text-zinc-700 hover:bg-zinc-100"
                }`}
              >
                <Icon className="h-4 w-4" strokeWidth={2} />
                <span>{label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="px-4 py-4 border-t border-border">
          <div className="label-overline mb-2">Status do Robô</div>
          <div className="flex items-center gap-2.5" data-testid="sidebar-robot-status">
            <span className={`inline-block h-2.5 w-2.5 rounded-full ${meta.dotColor} ${robotState === "running" ? "status-pulse" : ""}`} />
            <span className="text-sm font-medium">{meta.label}</span>
          </div>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 min-w-0 flex flex-col">
        <header className="h-14 border-b border-border bg-white flex items-center justify-between px-4 md:px-8">
          <button
            data-testid="menu-toggle"
            className="md:hidden p-2 -ml-2"
            onClick={() => setOpen(!open)}
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
          <div className="hidden md:flex items-center gap-2 text-sm">
            <span className="label-overline">Operações</span>
            <span className="text-zinc-400">/</span>
            <span className="font-medium">{NAV.find(n => n.to === location.pathname)?.label || "Painel"}</span>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <CircleDot className="h-3.5 w-3.5 text-emerald-500" />
            <span>backend conectado</span>
          </div>
        </header>

        <main className="flex-1 overflow-auto bg-zinc-50">
          <div className="max-w-[1400px] mx-auto px-4 md:px-8 py-6 md:py-8">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
