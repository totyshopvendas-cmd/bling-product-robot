import { useCallback, useEffect, useState } from "react";
import { Loader2, Lock } from "lucide-react";
import { api } from "@/lib/api";

/**
 * Protege o painel quando o servidor tem APP_PASSWORD definido.
 * No uso local (sem senha) renderiza os filhos direto.
 */
export default function AuthGate({ children }) {
  const [state, setState] = useState("checking"); // checking | ok | login
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const check = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/status");
      setState(data.required && !data.authenticated ? "login" : "ok");
    } catch {
      setState("ok"); // backend antigo/sem rota → não bloqueia
    }
  }, []);

  useEffect(() => {
    check();
    const onExpired = () => setState("login");
    window.addEventListener("totyshop:auth-required", onExpired);
    return () => window.removeEventListener("totyshop:auth-required", onExpired);
  }, [check]);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.post("/auth/login", { password });
      setPassword("");
      setState("ok");
    } catch (err) {
      setError(err?.response?.data?.detail || "Não foi possível entrar");
    } finally {
      setBusy(false);
    }
  };

  if (state === "checking") {
    return (
      <div className="min-h-screen flex items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (state === "login") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-zinc-50 px-4">
        <form onSubmit={submit} className="w-full max-w-sm bg-white border border-border p-8 space-y-5" data-testid="login-form">
          <div className="flex items-center gap-3">
            <img src="/logo.svg" alt="TotyShop" className="h-10 w-10" />
            <div className="font-display font-bold text-lg">
              <span className="text-zinc-900">Toty</span><span className="text-[#EE7B22]">Shop</span>
            </div>
          </div>
          <div>
            <label className="label-overline">Senha do painel</label>
            <input
              type="password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              data-testid="login-password"
              className="w-full text-sm border border-border rounded-sm px-3 py-2.5 mt-1 focus:outline-none focus:ring-2 focus:ring-[#EE7B22]"
            />
          </div>
          {error && <div className="text-sm text-rose-700">{error}</div>}
          <button
            type="submit"
            disabled={busy || !password}
            className="w-full bg-[#EE7B22] text-white text-sm font-medium px-5 py-2.5 rounded-sm hover:bg-[#C9651A] disabled:opacity-50 inline-flex items-center justify-center gap-2"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Lock className="h-4 w-4" />}
            Entrar
          </button>
        </form>
      </div>
    );
  }

  return children;
}
