import { useEffect, useState, useCallback } from "react";
import { endpoints } from "@/lib/api";
import { logger } from "@/lib/logger";
import {
  Activity, Database, KeyRound, Bot, CheckCircle2, AlertTriangle, Cog, ShoppingBag,
} from "lucide-react";
import { Link } from "react-router-dom";

const StatCard = ({ icon: Icon, label, value, sub, tone = "default", testId }) => {
  const tones = {
    default: "border-border",
    primary: "border-[#EE7B22]",
    success: "border-emerald-500",
    danger: "border-rose-500",
  };
  return (
    <div
      data-testid={testId}
      className={`bg-white border-l-2 ${tones[tone]} border-y border-r border-border p-5`}
    >
      <div className="flex items-start justify-between mb-3">
        <span className="label-overline">{label}</span>
        <Icon className="h-4 w-4 text-zinc-400" strokeWidth={2} />
      </div>
      <div className="font-display text-3xl font-bold tracking-tighter">{value}</div>
      {sub && <div className="text-xs text-muted-foreground mt-1">{sub}</div>}
    </div>
  );
};

export default function Dashboard() {
  const [stats, setStats] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await endpoints.dashboardStats();
      setStats(data);
    } catch (err) {
      logger.error("Failed to load dashboard stats:", err);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  if (!stats) return <div className="text-sm text-muted-foreground">Carregando…</div>;

  const robotMeta = {
    idle: { color: "bg-zinc-200 text-zinc-700", label: "OCIOSO" },
    running: { color: "bg-emerald-100 text-emerald-700", label: "RODANDO" },
    paused: { color: "bg-amber-100 text-amber-700", label: "PAUSADO" },
    error: { color: "bg-rose-100 text-rose-700", label: "ERRO" },
  }[stats.robot_state] || { color: "bg-zinc-100 text-zinc-700", label: stats.robot_state };

  const shopeeMeta = {
    idle: { color: "bg-zinc-200 text-zinc-700", label: "OCIOSO" },
    running: { color: "bg-emerald-100 text-emerald-700", label: "RODANDO" },
    paused: { color: "bg-amber-100 text-amber-700", label: "PAUSADO" },
    error: { color: "bg-rose-100 text-rose-700", label: "ERRO" },
  }[stats.shopee_state] || { color: "bg-zinc-100 text-zinc-700", label: stats.shopee_state };

  const robotTone =
    stats.robot_state === "running" ? "success" :
    stats.robot_state === "error" ? "danger" : "default";

  const shopeeTone =
    stats.shopee_state === "running" ? "success" :
    stats.shopee_state === "error" ? "danger" : "default";

  return (
    <div className="space-y-8">
      <div>
        <div className="label-overline mb-1">Operações TotyShop</div>
        <h1 className="font-display text-3xl md:text-4xl font-bold tracking-tighter">Painel de Controle</h1>
        <p className="text-sm text-muted-foreground mt-1">Visão geral de cadastros, robô e integrações.</p>
      </div>

      <div className="border border-border bg-white p-5 text-sm space-y-1">
        <div className="label-overline mb-2">O que falta para as engrenagens girarem</div>
        <p>{stats.pricing_rows > 0 ? "✓" : "✗"} Tabela de preços — {stats.pricing_rows.toLocaleString("pt-BR")} linhas</p>
        <p>{stats.johndrop_configured ? "✓" : "✗"} JohnDrop — e-mail e senha em Configurações</p>
        <p>{stats.bling_connected ? "✓" : "✗"} Bling — um clique em Configurações → Conectar Bling</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-0 border border-border bg-white">
        <div className="border-r border-b sm:border-b lg:border-b-0 border-border">
          <StatCard
            testId="stat-pricing"
            icon={Database}
            label="Linhas na tabela"
            value={stats.pricing_rows.toLocaleString("pt-BR")}
            sub="Tabela de preços CSV"
          />
        </div>
        <div className="border-r border-b lg:border-b-0 border-border">
          <StatCard
            testId="stat-bling"
            icon={KeyRound}
            tone={stats.bling_connected ? "success" : "danger"}
            label="Bling API"
            value={stats.bling_connected ? "Conectado" : "Desconectado"}
            sub={stats.bling_connected ? "OAuth ativo" : "Conecte em Configurações"}
          />
        </div>
        <div className="border-r border-b sm:border-b-0 border-border">
          <StatCard
            testId="stat-johndrop"
            icon={Cog}
            tone={stats.johndrop_configured ? "success" : "danger"}
            label="JohnDrop"
            value={stats.johndrop_configured ? "Configurado" : "Falta config."}
            sub={stats.johndrop_configured ? "Credenciais salvas" : "Configure credenciais"}
          />
        </div>
        <StatCard
          testId="stat-robot"
          icon={Bot}
          tone={robotTone}
          label="Robô JohnDrop"
          value={<span className={`inline-flex px-2 py-0.5 text-xs font-semibold rounded-sm ${robotMeta.color}`}>{robotMeta.label}</span>}
          sub={stats.products_processed_today + " processados hoje"}
        />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-0 border border-border bg-white">
        <div className="border-r border-b sm:border-b lg:border-b-0 border-border">
          <StatCard
            testId="stat-shopee-config"
            icon={ShoppingBag}
            tone={stats.shopee_configured ? "success" : "danger"}
            label="Shopee"
            value={stats.shopee_configured ? "Configurado" : "Falta config."}
            sub={stats.shopee_configured ? "Usa credenciais JohnDrop" : "Configure credenciais"}
          />
        </div>
        <StatCard
          testId="stat-shopee-robot"
          icon={Bot}
          tone={shopeeTone}
          label="Robô Shopee"
          value={<span className={`inline-flex px-2 py-0.5 text-xs font-semibold rounded-sm ${shopeeMeta.color}`}>{shopeeMeta.label}</span>}
          sub={stats.shopee_state === "running" ? "Em execução" : "Parado"}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-0 border border-border bg-white">
        <StatCard
          testId="stat-success-today"
          icon={CheckCircle2}
          tone="success"
          label="Sucessos hoje"
          value={stats.success_today}
        />
        <div className="border-x border-border">
          <StatCard
            testId="stat-failed-today"
            icon={AlertTriangle}
            tone="danger"
            label="Falhas hoje"
            value={stats.failed_today}
          />
        </div>
        <StatCard
          testId="stat-total-today"
          icon={Activity}
          label="Total processado"
          value={stats.products_processed_today}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Link
          to="/robo"
          data-testid="action-go-robot"
          className="border border-border bg-white p-6 hover:border-[#EE7B22] transition-colors"
        >
          <Bot className="h-5 w-5 mb-3 text-[#EE7B22]" strokeWidth={2} />
          <div className="font-display font-bold text-lg mb-1">Executar Robô</div>
          <p className="text-sm text-muted-foreground">
            Inicia o robô que limpa títulos automaticamente, busca preços na tabela e cadastra produtos no JohnDrop.
          </p>
        </Link>
        <Link
          to="/shopee"
          data-testid="action-go-shopee"
          className="border border-border bg-white p-6 hover:border-[#EE7B22] transition-colors"
        >
          <ShoppingBag className="h-5 w-5 mb-3 text-[#EE7B22]" strokeWidth={2} />
          <div className="font-display font-bold text-lg mb-1">Executar Robô Shopee</div>
          <p className="text-sm text-muted-foreground">
            Cadastra produtos na integração TotyShop-Shopee, escolhendo automaticamente uma categoria genérica compatível.
          </p>
        </Link>
        <Link
          to="/limpeza"
          data-testid="action-go-cleaner"
          className="border border-border bg-white p-6 hover:border-[#EE7B22] transition-colors"
        >
          <Activity className="h-5 w-5 mb-3 text-[#EE7B22]" strokeWidth={2} />
          <div className="font-display font-bold text-lg mb-1">Testar Limpeza de Título</div>
          <p className="text-sm text-muted-foreground">
            Cole um título cru do fornecedor e veja o título limpo seguindo as regras (máx 60 chars, código no final).
          </p>
        </Link>
      </div>
    </div>
  );
}
