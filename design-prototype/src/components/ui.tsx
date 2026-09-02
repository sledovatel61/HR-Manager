import {
  forwardRef,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { STATUS_META, type CandidateStatus } from "../data/mock";

export function StatusChip({ status }: { status: CandidateStatus }) {
  const meta = STATUS_META[status];
  return (
    <span className={`chip chip-${meta.tone}`}>
      <span className="chip-dot" aria-hidden="true" />
      {meta.label}
    </span>
  );
}

export function Avatar({ name, size = "md" }: { name: string; size?: "md" | "lg" }) {
  const parts = name.split(" ").filter(Boolean);
  const initials = ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase();
  return (
    <span className={`avatar${size === "lg" ? " avatar-lg" : ""}`} title={name} aria-hidden="true">
      {initials}
    </span>
  );
}

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  icon?: boolean;
}>(function Button(
  { variant = "secondary", size = "md", icon, className = "", children, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={`btn btn-${variant}${size === "sm" ? " btn-sm" : ""}${icon ? " btn-icon" : ""} ${className}`.trim()}
      {...rest}
    >
      {children}
    </button>
  );
});

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
  htmlFor?: string;
}) {
  const autoId = useId();
  const id = htmlFor ?? autoId;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {/* clone-less: caller should set id; we pass via data */}
      <div data-field-id={id}>{children}</div>
      {error ? (
        <span className="field-error" role="alert" id={`${id}-err`}>
          {error}
        </span>
      ) : hint ? (
        <span className="field-hint muted">{hint}</span>
      ) : null}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { error?: boolean }>(
  function Input({ error, className = "", ...rest }, ref) {
    return <input ref={ref} className={`input${error ? " is-error" : ""} ${className}`.trim()} {...rest} />;
  },
);

export function PasswordInput(props: InputHTMLAttributes<HTMLInputElement> & { error?: boolean }) {
  const [show, setShow] = useState(false);
  return (
    <div className="input-wrap">
      <Input {...props} type={show ? "text" : "password"} autoComplete={props.autoComplete ?? "current-password"} />
      <Button
        icon
        variant="ghost"
        aria-label={show ? "Скрыть пароль" : "Показать пароль"}
        aria-pressed={show}
        onClick={() => setShow((v) => !v)}
        type="button"
      >
        {show ? "🙈" : "👁"}
      </Button>
    </div>
  );
}

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className = "", children, ...rest }, ref) {
    return (
      <select ref={ref} className={`select ${className}`.trim()} {...rest}>
        {children}
      </select>
    );
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement> & { error?: boolean }>(
  function Textarea({ error, className = "", ...rest }, ref) {
    return <textarea ref={ref} className={`textarea${error ? " is-error" : ""} ${className}`.trim()} {...rest} />;
  },
);

export function Segmented({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  large,
  initialFocusRef,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  large?: boolean;
  initialFocusRef?: React.RefObject<HTMLElement | null>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const node = initialFocusRef?.current ?? ref.current?.querySelector<HTMLElement>("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])");
    node?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
      if (e.key === "Tab" && ref.current) {
        const focusables = Array.from(
          ref.current.querySelectorAll<HTMLElement>(
            "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
          ),
        ).filter((el) => el.offsetParent !== null || el === document.activeElement);
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      previouslyFocused.current?.focus?.();
    };
  }, [open, onClose, initialFocusRef]);

  if (!open) return null;
  return (
    <div className="overlay" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className={`dialog${large ? " dialog-lg" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={ref}
      >
        <header>
          <h2 id={titleId}>{title}</h2>
          <Button variant="ghost" icon aria-label="Закрыть" onClick={onClose}>
            ✕
          </Button>
        </header>
        <div className="body">{children}</div>
        {footer ? <footer>{footer}</footer> : null}
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon = "◇",
}: {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: string;
}) {
  return (
    <div className="state-block card card-pad">
      <div className="state-icon" aria-hidden="true">
        {icon}
      </div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function SkeletonPage() {
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">Загрузка…</span>
      <div className="stack" style={{ gap: 16 }}>
        <div className="skeleton" style={{ width: 220, height: 28 }} />
        <div className="skeleton" style={{ width: 360, height: 14 }} />
        <div className="grid-kpi">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="card card-pad">
              <div className="skeleton" style={{ width: "40%", marginBottom: 12 }} />
              <div className="skeleton" style={{ width: "55%", height: 28 }} />
            </div>
          ))}
        </div>
        <div className="skeleton skeleton-block" />
        <div className="skeleton skeleton-block" style={{ height: 240 }} />
      </div>
    </div>
  );
}
