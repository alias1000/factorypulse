import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@fontsource-variable/inter";
import "./index.css";
import App from "./App";
import { ApiError } from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";
import { FilterProvider } from "@/lib/filters";
import { ToastProvider } from "@/components/Toast";
import { AuthProvider } from "@/lib/auth";
import { AuthGate } from "@/components/AuthGate";
import { TooltipProvider } from "@/components/ui/tooltip";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // never retry auth/permission errors; retry transient failures once
      retry: (count, err) => count < 1 && !(err instanceof ApiError && [401, 403, 404, 422].includes(err.status)),
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <TooltipProvider delayDuration={150}>
          <ToastProvider>
            <AuthProvider>
              <AuthGate>
                <FilterProvider>
                  <App />
                </FilterProvider>
              </AuthGate>
            </AuthProvider>
          </ToastProvider>
        </TooltipProvider>
      </I18nProvider>
    </QueryClientProvider>
  </StrictMode>,
);
