import { forwardRef, type ButtonHTMLAttributes } from "react";
import { Icon, type IconName } from "../../icons/Icon";
import "./button.css";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: IconName;
  iconPosition?: "left" | "right";
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon, iconPosition = "left", loading, disabled, className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      className={["btn", `btn-${variant}`, `btn-${size}`, className].filter(Boolean).join(" ")}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? (
        <span className="btn-spinner" aria-hidden="true" />
      ) : (
        icon && iconPosition === "left" && <Icon name={icon} size={16} />
      )}
      <span className="btn-label">{children}</span>
      {!loading && icon && iconPosition === "right" && <Icon name={icon} size={16} />}
    </button>
  );
});

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: IconName;
  label: string;
  size?: ButtonSize;
  variant?: "ghost" | "secondary";
  active?: boolean;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon, label, size = "md", variant = "ghost", active, className, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      className={["icon-btn", `icon-btn-${size}`, `icon-btn-${variant}`, active ? "is-active" : "", className]
        .filter(Boolean)
        .join(" ")}
      aria-label={label}
      title={label}
      aria-pressed={active}
      {...rest}
    >
      <Icon name={icon} size={size === "sm" ? 14 : 16} />
    </button>
  );
});
