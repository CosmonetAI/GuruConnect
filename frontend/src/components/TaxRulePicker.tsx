import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Check, ChevronDown, RefreshCw } from "lucide-react";
import "../styles/tax-rule-picker.css";

export type TaxRule = {
  tax_id?: string;
  tax_name?: string;
  tax_display_name?: string;
  tax_percentage?: number | string;
  tax_type?: string;
  tax_specification?: string;
};

type TaxRulePickerProps = {
  taxes?: TaxRule[];
  selectedTaxId?: string;
  onTaxChange?: (taxId: string) => void;
  onRefreshTaxes?: () => void;
  taxRefreshBusy?: boolean;
  taxesUpdatedAt?: string | null;
  className?: string;
};

function taxName(tax: TaxRule) {
  return tax.tax_display_name || tax.tax_name || "Tax rule";
}

function taxRate(tax: TaxRule) {
  return `${tax.tax_percentage ?? 0}%`;
}

function taxDescription(tax: TaxRule) {
  if (tax.tax_type !== "tax_group") return "Tax rule from Zoho Books";
  const specification = String(tax.tax_specification || "").toLowerCase();
  if (specification === "intra") return "Intra-state GST group from Zoho Books";
  if (specification === "inter") return "Inter-state GST group from Zoho Books";
  return "GST group from Zoho Books";
}

export default function TaxRulePicker({
  taxes = [],
  selectedTaxId = "",
  onTaxChange,
  onRefreshTaxes,
  taxRefreshBusy = false,
  taxesUpdatedAt,
  className = "",
}: TaxRulePickerProps) {
  const [open, setOpen] = useState(false);
  const [menuStyle, setMenuStyle] = useState<CSSProperties>({});
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const selected = taxes.find(
    (tax) => String(tax.tax_id || "") === String(selectedTaxId),
  );

  const positionMenu = () => {
    const bounds = trigger.current?.getBoundingClientRect();
    if (!bounds) return;
    const menuHeight = Math.min(350, Math.max(100, (taxes.length + 1) * 54 + 28));
    const openUp = bounds.bottom + menuHeight > window.innerHeight - 16 && bounds.top > menuHeight + 16;
    setMenuStyle({
      left: bounds.left,
      top: openUp ? Math.max(16, bounds.top - menuHeight - 6) : bounds.bottom + 6,
      width: bounds.width,
    });
  };

  useEffect(() => {
    if (!open) return undefined;
    positionMenu();
    const reposition = () => positionMenu();
    const closeOnOutside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open, taxes.length]);

  // No configured rules means there is no useful selector. Settings remains
  // the place where an owner can inspect or refresh tax configuration.
  if (!taxes.length) return null;

  const choose = (taxId: string) => {
    onTaxChange?.(taxId);
    setOpen(false);
  };

  return (
    <div className={`tax-rule-picker ${className}`.trim()} ref={root}>
      <span className="tax-rule-picker-label">GST / tax rule</span>
      <div className="tax-rule-picker-row">
        <button
          ref={trigger}
          type="button"
          className={`tax-rule-picker-trigger${open ? " is-open" : ""}`}
          aria-haspopup="listbox"
          aria-expanded={open}
          onClick={() => setOpen((current) => !current)}
        >
          <span className="tax-rule-picker-copy">
            <strong>
              {selected
                ? taxName(selected)
                : taxes.length
                  ? "No tax"
                  : "No tax rules loaded"}
            </strong>
            <small>
              {selected
                ? taxDescription(selected)
                : taxes.length
                  ? "Optional — no GST applied"
                  : "Refresh to check Zoho Books"}
            </small>
          </span>
          <span className="tax-rule-picker-rate">
            {selected ? taxRate(selected) : "—"}
          </span>
          <ChevronDown size={16} aria-hidden="true" />
        </button>
        {onRefreshTaxes && (
          <button
            type="button"
            className="tax-rule-picker-refresh"
            aria-label="Refresh GST and tax rules"
            title={
              taxesUpdatedAt
                ? `Last refreshed ${new Date(taxesUpdatedAt).toLocaleString()}`
                : "Refresh tax rules from Zoho Books"
            }
            onClick={onRefreshTaxes}
            disabled={taxRefreshBusy}
          >
            <RefreshCw size={16} className={taxRefreshBusy ? "tax-rule-picker-spin" : ""} />
          </button>
        )}
      </div>
      {open && (
        <div
          className="tax-rule-picker-menu"
          role="listbox"
          aria-label="GST and tax rules"
          style={menuStyle}
        >
          <button
            type="button"
            role="option"
            aria-selected={!selectedTaxId}
            className={`tax-rule-picker-option${!selectedTaxId ? " is-selected" : ""}`}
            onClick={() => choose("")}
          >
            <span className="tax-rule-picker-option-copy">
              <strong>No tax</strong>
              <small>Do not apply a GST rule</small>
            </span>
            {!selectedTaxId && <Check size={16} aria-hidden="true" />}
          </button>
          {taxes.map((tax) => {
            const id = String(tax.tax_id || "");
            const isSelected = id === String(selectedTaxId);
            return (
              <button
                type="button"
                role="option"
                aria-selected={isSelected}
                className={`tax-rule-picker-option${isSelected ? " is-selected" : ""}`}
                key={id || taxName(tax)}
                onClick={() => choose(id)}
              >
                <span className="tax-rule-picker-option-copy">
                  <strong>{taxName(tax)}</strong>
                  <small>
                    {tax.tax_type === "tax_group" ? "Tax group" : "Tax rule"} · Zoho Books
                  </small>
                </span>
                <span className="tax-rule-picker-option-rate">
                  {taxRate(tax)}
                  {isSelected && <Check size={15} aria-hidden="true" />}
                </span>
              </button>
            );
          })}
          {!taxes.length && (
            <div className="tax-rule-picker-empty">
              No GST rules loaded. Use refresh to check Zoho Books again.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
