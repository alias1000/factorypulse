import { lazy, Suspense } from "react";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { Layout } from "@/components/Layout";
import { LoadingState } from "@/components/States";
import { RequirePermission } from "@/components/AuthGate";
import { useI18n } from "@/lib/i18n";

const Overview = lazy(() => import("@/pages/Overview"));
const Production = lazy(() => import("@/pages/Production"));
const Quality = lazy(() => import("@/pages/Quality"));
const Downtime = lazy(() => import("@/pages/Downtime"));
const Insights = lazy(() => import("@/pages/Insights"));
const Reports = lazy(() => import("@/pages/Reports"));
const ImportPage = lazy(() => import("@/pages/Import"));
const Settings = lazy(() => import("@/pages/Settings"));
const Admin = lazy(() => import("@/pages/Admin"));
const AcOverview = lazy(() => import("@/pages/actions/Overview"));
const AcList = lazy(() => import("@/pages/actions/List"));
const AcCreate = lazy(() => import("@/pages/actions/Create"));
const AcDetail = lazy(() => import("@/pages/actions/Detail"));
const AcMine = lazy(() => import("@/pages/actions/MyActions"));

function NotFound() {
  const { t } = useI18n();
  return (
    <div className="py-20 text-center">
      <div className="text-5xl font-bold text-slate-300">404</div>
      <Link className="mt-4 inline-block text-primary hover:underline" to="/">{t("nav.overview")}</Link>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<LoadingState className="min-h-screen" />}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="production" element={<Production />} />
            <Route path="quality" element={<Quality />} />
            <Route path="downtime" element={<Downtime />} />
            <Route path="insights" element={<Insights />} />
            <Route path="reports" element={<Reports />} />
            <Route path="import" element={<RequirePermission permission="import.read"><ImportPage /></RequirePermission>} />
            <Route path="admin" element={<RequirePermission permission="user.manage"><Admin /></RequirePermission>} />
            <Route path="settings" element={<Settings />} />
            <Route path="actions" element={<RequirePermission permission="action.read"><AcOverview /></RequirePermission>} />
            <Route path="actions/investigations" element={<RequirePermission permission="action.read"><AcList /></RequirePermission>} />
            <Route path="actions/investigations/new" element={<RequirePermission permission="action.write"><AcCreate /></RequirePermission>} />
            <Route path="actions/investigations/:id" element={<RequirePermission permission="action.read"><AcDetail /></RequirePermission>} />
            <Route path="actions/mine" element={<RequirePermission permission="action.read"><AcMine /></RequirePermission>} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
