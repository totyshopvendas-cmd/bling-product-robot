import "@/App.css";
import "@/index.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";
import Layout from "@/components/Layout";
import Dashboard from "@/pages/Dashboard";
import TitleCleaner from "@/pages/TitleCleaner";
import Pricing from "@/pages/Pricing";
import Robot from "@/pages/Robot";
import ShopeeRobot from "@/pages/ShopeeRobot";
import Logs from "@/pages/Logs";
import Settings from "@/pages/Settings";
import BlingEnrichment from "@/pages/BlingEnrichment";
import BlingBulkEnrich from "@/pages/BlingBulkEnrich";
import StockSync from "@/pages/StockSync";
import CategoryMapping from "@/pages/CategoryMapping";
import SocialSettings from "@/pages/SocialSettings";
import CreateAd from "@/pages/CreateAd";
import Schedule from "@/pages/Schedule";
import Onboarding from "@/pages/Onboarding";
import EnrichmentProgress from "@/pages/EnrichmentProgress";

function App() {
  return (
    <BrowserRouter>
      <Toaster position="top-right" richColors closeButton />
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/limpeza" element={<TitleCleaner />} />
          <Route path="/precos" element={<Pricing />} />
          <Route path="/robo" element={<Robot />} />
          <Route path="/shopee" element={<ShopeeRobot />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/bling" element={<BlingEnrichment />} />
          <Route path="/bling-lote" element={<BlingBulkEnrich />} />
          <Route path="/estoque-sync" element={<StockSync />} />
          <Route path="/categorias-multiloja" element={<CategoryMapping />} />
          <Route path="/redes-sociais" element={<SocialSettings />} />
          <Route path="/criar-anuncio" element={<CreateAd />} />
          <Route path="/agenda" element={<Schedule />} />
          <Route path="/setup-redes" element={<Onboarding />} />
          <Route path="/progresso" element={<EnrichmentProgress />} />
          <Route path="/configuracoes" element={<Settings />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;
