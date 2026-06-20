import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { logger } from "@/lib/logger";
import {
  CheckCircle2, AlertTriangle, XCircle, Circle, ExternalLink,
  RefreshCw, Loader2, Rocket, ChevronRight, Sparkles,
} from "lucide-react";

const STATUS_VISUAL = {
  ok: { Icon: CheckCircle2, color: "text-emerald-600", bg: "bg-emerald-50", border: "border-emerald-200", label: "Pronto" },
  warning: { Icon: AlertTriangle, color: "text-amber-600", bg: "bg-amber-50", border: "border-amber-200", label: "Atenção" },
  error: { Icon: XCircle, color: "text-rose-600", bg: "bg-rose-50", border: "border-rose-200", label: "Erro" },
  pending: { Icon: Circle, color: "text-zinc-400", bg: "bg-zinc-50", border: "border-zinc-200", label: "Pendente" },
};

const META_GUIDE = [
  {
    title: "1. Gerar token novo no Graph API Explorer",
    steps: [
      <>Abra <a className="text-[#EE7B22] underline" href="https://developers.facebook.com/tools/explorer/" target="_blank" rel="noreferrer">developers.facebook.com/tools/explorer</a></>,
      "No topo direito, selecione seu app (Meta App).",
      "Em 'User or Page' escolha User Token.",
      "Marque as permissões: pages_show_list, pages_manage_posts, pages_read_engagement, instagram_basic, instagram_content_publish, business_management.",
      "Clique Generate Access Token → autorize → copie o token gerado.",
    ],
  },
  {
    title: "2. Cole no TotyShop",
    steps: [
      <>Abra <Link to="/redes-sociais" className="text-[#EE7B22] underline">Redes Sociais</Link></>,
      "Cole o token no campo 'Token de Acesso da Página' → clique Salvar credenciais.",
      "Clique no botão verde 'Tornar Token Vitalício' (converte o token de 1h em um que nunca expira).",
      "Clique no botão azul 'Escolher Página' → selecione TotyShop.com.",
    ],
  },
  {
    title: "3. Vincular Instagram Business à página",
    steps: [
      <>Abra <a className="text-[#EE7B22] underline" href="https://business.facebook.com/settings/instagram-accounts" target="_blank" rel="noreferrer">business.facebook.com → Contas do Instagram</a></>,
      "Adicionar → conecte @totyshop4.",
      "Vá em Páginas → TotyShop.com → Configurações → Instagram conectado → confirme.",
      "Volte no TotyShop → Redes Sociais → clique Testar conexão. Deve aparecer 'Instagram Business: vinculado'.",
    ],
  },
];

const PINTEREST_GUIDE = [
  {
    title: "1. Sair do Sandbox (Apply for Production)",
    steps: [
      <>Abra <a className="text-[#EE7B22] underline" href="https://developers.pinterest.com/apps/" target="_blank" rel="noreferrer">developers.pinterest.com/apps</a></>,
      "Clique no seu app → aba Apply for Production.",
      "Preencha o formulário. Sugestão de use case: 'Auto-pin product images from our Bling ERP catalog (TotyShop)'.",
      "Submeta. Aprovação: 1-3 dias úteis.",
    ],
  },
  {
    title: "2. Após aprovação, gerar Token Produção",
    steps: [
      "Quando o status mudar para Production, gere um novo Access Token (não Sandbox).",
      <>Em <Link to="/redes-sociais" className="text-[#EE7B22] underline">Redes Sociais</Link>, role até a seção Pinterest e cole o token.</>,
      "Clique Testar e buscar boards → selecione o board padrão (ex: Produtos TotyShop).",
    ],
  },
];

const YOUTUBE_GUIDE = [
  {
    title: "1. Criar projeto + habilitar API no Google Cloud",
    steps: [
      <>Abra <a className="text-[#EE7B22] underline" href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noreferrer">console.cloud.google.com</a> e crie um projeto (ex: &quot;TotyShop YouTube&quot;)</>,
      <>Em <strong>APIs &amp; Services → Library</strong>, procure &quot;YouTube Data API v3&quot; e clique <strong>Enable</strong></>,
      "Volte em APIs & Services → OAuth consent screen → External → preencha (nome, email, depois Save)",
    ],
  },
  {
    title: "2. Criar OAuth Client ID",
    steps: [
      "APIs & Services → Credentials → Create Credentials → OAuth client ID",
      "Application type: Web application",
      <>Em <strong>Authorized redirect URIs</strong>, cole exatamente: <code className="bg-zinc-100 px-1.5 py-0.5 rounded text-xs">{window.location.origin}/api/social/youtube/oauth/callback</code></>,
      "Save. Copie Client ID + Client Secret",
    ],
  },
  {
    title: "3. Conectar no TotyShop",
    steps: [
      <>Em <Link to="/redes-sociais" className="text-[#EE7B22] underline">Redes Sociais</Link>, role até a seção YouTube</>,
      "Cole Client ID + Secret → Salvar",
      "Clique no botão verde Conectar YouTube → autorize → o sistema captura o refresh token automaticamente",
      "Pronto! Agora cada anúncio gerado pode virar um Short automaticamente (imagem 9:16 + voz da Nova lendo o copy + upload).",
    ],
  },
];


export default function OnboardingPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    try {
      const { data } = await api.get("/social/onboarding/status");
      setData(data);
    } catch (e) {
      logger.error("onboarding status", e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get("/social/onboarding/status");
        if (!cancelled) setData(data);
      } catch (e) {
        logger.error("onboarding status", e);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="py-16 text-center text-muted-foreground">
        <Loader2 className="h-6 w-6 mx-auto animate-spin mb-2" />
        Verificando integrações…
      </div>
    );
  }

  const summary = data?.summary || {};
  const groups = data?.groups || {};
  const next = data?.next_step;
  const progress = summary.total ? Math.round((summary.ok / summary.total) * 100) : 0;
  const allGreen = summary.errors === 0 && summary.pending === 0 && summary.warnings === 0;

  return (
    <div className="space-y-6" data-testid="onboarding-page">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-display font-bold flex items-center gap-2">
            <Rocket className="h-6 w-6 text-[#EE7B22]" /> Setup de Redes Sociais
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Checklist passo-a-passo para deixar Instagram, Facebook e Pinterest publicando automaticamente.
          </p>
        </div>
        <button
          onClick={() => { setRefreshing(true); load(); }}
          data-testid="refresh-status"
          className="px-3 py-2 text-sm bg-zinc-100 hover:bg-zinc-200 rounded-sm flex items-center gap-2"
        >
          <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          Verificar de novo
        </button>
      </header>

      {/* Progress card */}
      <div className={`rounded-sm border p-5 ${allGreen ? "bg-emerald-50 border-emerald-300" : "bg-white border-border"}`}>
        <div className="flex items-center justify-between mb-3">
          <div className="font-semibold">Progresso geral</div>
          <div className="text-sm">
            <span className="text-emerald-700 font-semibold">{summary.ok}</span>
            <span className="text-zinc-400"> / {summary.total} prontos</span>
          </div>
        </div>
        <div className="h-2 rounded-sm bg-zinc-200 overflow-hidden">
          <div
            className={`h-full transition-all ${allGreen ? "bg-emerald-500" : "bg-[#EE7B22]"}`}
            style={{ width: `${progress}%` }}
          />
        </div>
        <div className="grid grid-cols-4 gap-2 mt-4 text-xs">
          <Pill icon={CheckCircle2} count={summary.ok} label="Prontos" color="emerald" />
          <Pill icon={AlertTriangle} count={summary.warnings} label="Atenção" color="amber" />
          <Pill icon={XCircle} count={summary.errors} label="Erros" color="rose" />
          <Pill icon={Circle} count={summary.pending} label="Pendentes" color="zinc" />
        </div>

        {summary.ready_to_post && (
          <div className="mt-4 p-3 rounded-sm bg-emerald-100 border border-emerald-300 text-sm flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-emerald-700" />
            <span className="text-emerald-900">
              <strong>Pronto para publicar!</strong> Você já pode ir em{" "}
              <Link to="/criar-anuncio" className="underline font-semibold">Criar Anúncio</Link> e
              postar um produto no Facebook (e Instagram se estiver vinculado).
            </span>
          </div>
        )}

        {next && !allGreen && (
          <div className="mt-4 p-3 rounded-sm bg-blue-50 border border-blue-300 text-sm" data-testid="next-action">
            <div className="font-semibold flex items-center gap-1 text-blue-900">
              <ChevronRight className="h-4 w-4" /> Próxima ação:
            </div>
            <div className="mt-1">{next.label} — <span className="text-zinc-700">{next.detail}</span></div>
            {next.action_route && (
              <Link
                to={next.action_route}
                className="inline-block mt-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs rounded-sm"
              >
                Ir para {next.action_route === "/redes-sociais" ? "Redes Sociais" : next.action_route}
              </Link>
            )}
            {next.action_external_url && (
              <a
                href={next.action_external_url}
                target="_blank" rel="noreferrer"
                className="inline-block mt-2 ml-2 px-3 py-1.5 bg-zinc-100 hover:bg-zinc-200 text-xs rounded-sm"
              >
                <ExternalLink className="h-3 w-3 inline mr-1" />
                Abrir link externo
              </a>
            )}
          </div>
        )}
      </div>

      {/* Meta section */}
      <Section
        title="Meta (Instagram + Facebook)"
        steps={groups.meta || []}
        guide={META_GUIDE}
      />

      {/* Pinterest section */}
      <Section
        title="Pinterest"
        steps={groups.pinterest || []}
        guide={PINTEREST_GUIDE}
      />

      {/* YouTube Shorts section */}
      <Section
        title="YouTube Shorts"
        steps={groups.youtube || []}
        guide={YOUTUBE_GUIDE}
      />
    </div>
  );
}


function Pill({ icon: Icon, count, label, color }) {
  const map = {
    emerald: "bg-emerald-100 text-emerald-700",
    amber: "bg-amber-100 text-amber-700",
    rose: "bg-rose-100 text-rose-700",
    zinc: "bg-zinc-100 text-zinc-600",
  };
  return (
    <div className={`flex items-center gap-1.5 px-2 py-1 rounded-sm ${map[color]}`}>
      <Icon className="h-3.5 w-3.5" />
      <strong>{count}</strong> <span>{label}</span>
    </div>
  );
}


function Section({ title, steps, guide }) {
  const [openIdx, setOpenIdx] = useState(null);
  return (
    <section className="rounded-sm border border-border bg-white p-5 space-y-4" data-testid={`section-${title.toLowerCase().replace(/\s+/g, "-")}`}>
      <h2 className="font-semibold text-lg">{title}</h2>

      {/* Status checklist */}
      <div className="space-y-2">
        {steps.map((s) => {
          const v = STATUS_VISUAL[s.status] || STATUS_VISUAL.pending;
          return (
            <div
              key={s.id}
              data-testid={`step-${s.id}`}
              className={`flex items-start gap-3 p-3 rounded-sm border ${v.border} ${v.bg}`}
            >
              <v.Icon className={`h-5 w-5 ${v.color} flex-shrink-0 mt-0.5`} />
              <div className="flex-1">
                <div className="text-sm font-medium">{s.label}</div>
                <div className="text-xs text-zinc-700 mt-0.5">{s.detail}</div>
              </div>
              {s.action_external_url && (
                <a
                  href={s.action_external_url}
                  target="_blank" rel="noreferrer"
                  data-testid={`ext-${s.id}`}
                  className="text-xs text-blue-700 hover:underline flex items-center gap-1 flex-shrink-0"
                >
                  Abrir <ExternalLink className="h-3 w-3" />
                </a>
              )}
              {s.action_route && (
                <Link
                  to={s.action_route}
                  data-testid={`route-${s.id}`}
                  className="text-xs text-blue-700 hover:underline flex-shrink-0"
                >
                  Resolver →
                </Link>
              )}
            </div>
          );
        })}
      </div>

      {/* Guide (expandable) */}
      <div className="border-t border-border pt-3">
        <div className="text-xs uppercase tracking-wide font-semibold text-zinc-500 mb-2">
          Passo-a-passo
        </div>
        {guide.map((g, i) => (
          <div key={i} className="mb-2">
            <button
              onClick={() => setOpenIdx(openIdx === i ? null : i)}
              data-testid={`guide-toggle-${i}`}
              className="w-full text-left px-3 py-2 text-sm font-medium bg-zinc-50 hover:bg-zinc-100 rounded-sm flex items-center justify-between"
            >
              <span>{g.title}</span>
              <ChevronRight className={`h-4 w-4 transition-transform ${openIdx === i ? "rotate-90" : ""}`} />
            </button>
            {openIdx === i && (
              <ol className="mt-2 ml-4 space-y-1.5 text-sm list-decimal">
                {g.steps.map((step, j) => <li key={j} className="text-zinc-700">{step}</li>)}
              </ol>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
