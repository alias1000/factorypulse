import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold", {
  variants: {
    variant: {
      default: "border-transparent bg-primary/10 text-primary",
      secondary: "border-transparent bg-slate-100 text-slate-700",
      success: "border-transparent bg-emerald-50 text-emerald-700",
      warning: "border-transparent bg-amber-50 text-amber-700",
      danger: "border-transparent bg-red-50 text-red-700",
      critical: "border-transparent bg-red-600 text-white",
      high: "border-transparent bg-orange-100 text-orange-800",
      medium: "border-transparent bg-yellow-100 text-yellow-800",
      low: "border-transparent bg-sky-100 text-sky-800",
      outline: "text-slate-700",
      teal: "border-transparent bg-teal-50 text-teal-700",
    },
  },
  defaultVariants: { variant: "default" },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}
export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
