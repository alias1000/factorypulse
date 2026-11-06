import type { ReactNode } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartSkeleton, EmptyState, ErrorState } from "./States";
import { cn } from "@/lib/utils";

interface Props {
  title: string;
  description?: string;
  loading?: boolean;
  error?: unknown;
  empty?: boolean;
  onRetry?: () => void;
  height?: number;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}

export function ChartCard({ title, description, loading, error, empty, onRetry, height = 260, actions, className, children }: Props) {
  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle>{title}</CardTitle>
          {description && <CardDescription className="mt-1">{description}</CardDescription>}
        </div>
        {actions}
      </CardHeader>
      <CardContent className="flex-1">
        {error ? (
          <ErrorState error={error} onRetry={onRetry} />
        ) : loading ? (
          <ChartSkeleton height={height} />
        ) : empty ? (
          <EmptyState />
        ) : (
          <div style={{ minHeight: height }}>{children}</div>
        )}
      </CardContent>
    </Card>
  );
}
