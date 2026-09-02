import { cloneElement, useId, useState, type ReactElement } from "react";
import "./tooltip.css";

interface TooltipProps {
  label: string;
  children: ReactElement<{ "aria-describedby"?: string }>;
}

/** Simple hover/focus tooltip. Content is also reachable via focus, not just hover. */
export function Tooltip({ label, children }: TooltipProps) {
  const [visible, setVisible] = useState(false);
  const id = useId();

  return (
    <span
      className="tooltip-wrapper"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
      onFocus={() => setVisible(true)}
      onBlur={() => setVisible(false)}
    >
      {cloneElement(children, { "aria-describedby": id })}
      <span role="tooltip" id={id} className="tooltip-bubble" data-visible={visible || undefined}>
        {label}
      </span>
    </span>
  );
}
