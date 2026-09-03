import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import "./field.css";

interface FieldProps {
  label: string;
  hint?: string;
  error?: string;
  children: (id: string, describedBy: string | undefined) => ReactNode;
  required?: boolean;
}

/** Обёртка label+hint+error c корректной связкой aria-describedby. */
export function Field({ label, hint, error, children, required }: FieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className="field">
      <label htmlFor={id} className="field-label">
        {label}
        {required && <span aria-hidden="true"> *</span>}
        {required && <span className="sr-only"> обязательное поле</span>}
      </label>
      {children(id, describedBy)}
      {hint && !error && (
        <p id={hintId} className="field-hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="field-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }>(
  function TextInput({ className, invalid, ...rest }, ref) {
    return (
      <input
        ref={ref}
        className={["text-input", invalid ? "is-invalid" : "", className].filter(Boolean).join(" ")}
        aria-invalid={invalid || undefined}
        {...rest}
      />
    );
  },
);

export const SelectInput = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function SelectInput(
  { className, children, ...rest },
  ref,
) {
  return (
    <select ref={ref} className={["select-input", className].filter(Boolean).join(" ")} {...rest}>
      {children}
    </select>
  );
});
