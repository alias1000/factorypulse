import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { I18nProvider } from "@/lib/i18n";
import { TooltipProvider } from "@/components/ui/tooltip";
import { DataTable } from "@/components/DataTable";
import { KpiCard } from "@/components/KpiCard";

const wrap = (ui: React.ReactNode) => render(<I18nProvider initial="en"><TooltipProvider>{ui}</TooltipProvider></I18nProvider>);

describe("DataTable", () => {
  const rows = [
    { name: "M-01", oee: 0.7 }, { name: "M-02", oee: null }, { name: "M-03", oee: 0.8 },
  ];
  const cols = [
    { key: "name", header: "Machine", value: (r: (typeof rows)[0]) => r.name },
    { key: "oee", header: "OEE", numeric: true, value: (r: (typeof rows)[0]) => r.oee },
  ];
  it("sorts and keeps N/A values last", () => {
    wrap(<DataTable rows={rows} columns={cols} rowKey={(r) => r.name} />);
    fireEvent.click(screen.getByRole("button", { name: /OEE/ }));
    const body = screen.getAllByRole("row").slice(1);
    expect(within(body[0]).getByText("M-03")).toBeInTheDocument();
    expect(within(body[2]).getByText("M-02")).toBeInTheDocument();
  });
  it("searches", () => {
    wrap(<DataTable rows={rows} columns={cols} rowKey={(r) => r.name} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "03" } });
    expect(screen.getAllByRole("row")).toHaveLength(2);
  });
});

describe("KpiCard", () => {
  it("renders N/A with explanation affordance", () => {
    wrap(<KpiCard label="OEE" value="N/A" metric={{ value: null, records_used: 0, records_total: 5, unit: "ratio", coverage: 0 }} formula="A × P × Q" testId="k" />);
    expect(screen.getByTestId("k-value")).toHaveTextContent("N/A");
    expect(screen.getByRole("button", { name: /OEE: Formula/ })).toBeInTheDocument();
  });
  it("shows partial data coverage", () => {
    wrap(<KpiCard label="OEE" value="70 %" metric={{ value: 0.7, records_used: 3, records_total: 4, unit: "ratio", coverage: 0.75 }} />);
    expect(screen.getByText("Based on 3 of 4 records")).toBeInTheDocument();
  });
});

describe("ConfirmDialog", () => {
  it("requires the confirmation word and the password before confirming", async () => {
    const { ConfirmDialog } = await import("@/components/ConfirmDialog");
    let received = "";
    wrap(<ConfirmDialog title="Restore" description="d" confirmLabel="Go" requireText="RESTORE" requirePassword
      onConfirm={(pw) => { received = pw; }} trigger={(open) => <button onClick={open}>open</button>} />);
    fireEvent.click(screen.getByText("open"));
    const ok = screen.getByTestId("confirm-ok");
    expect(ok).toBeDisabled();
    fireEvent.change(screen.getByTestId("confirm-input"), { target: { value: "RESTORE" } });
    expect(ok).toBeDisabled();
    fireEvent.change(screen.getByTestId("confirm-password"), { target: { value: "secret-pass" } });
    expect(ok).toBeEnabled();
    fireEvent.click(ok);
    await new Promise((r) => setTimeout(r, 0));
    expect(received).toBe("secret-pass");
  });
});
