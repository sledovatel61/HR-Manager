/**
 * Компактный набор line-иконок для дизайн-прототипа.
 *
 * Все иконки — оригинальные inline SVG-пути (никаких внешних иконных
 * шрифтов/CDN, ноль runtime-запросов). Единый размер через токен
 * --icon-size-*, единая толщина линии.
 */
import type { SVGProps } from "react";

export type IconName =
  | "search"
  | "command"
  | "close"
  | "chevron-down"
  | "chevron-right"
  | "chevron-left"
  | "chevron-up-down"
  | "plus"
  | "filter"
  | "sort"
  | "kanban"
  | "table"
  | "calendar"
  | "users"
  | "shield"
  | "file-text"
  | "home"
  | "inbox"
  | "bar-chart"
  | "settings"
  | "log-out"
  | "more-horizontal"
  | "phone"
  | "mail"
  | "clock"
  | "check-circle"
  | "alert-triangle"
  | "alert-octagon"
  | "arrow-right-left"
  | "star"
  | "bell"
  | "sun"
  | "moon"
  | "layout-grid"
  | "list"
  | "download"
  | "lock"
  | "eye"
  | "edit"
  | "trash"
  | "undo"
  | "loader"
  | "wifi-off"
  | "check"
  | "user-plus"
  | "info"
  | "spark";

const PATHS: Record<IconName, string> = {
  search:
    '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  command:
    '<path d="M8 3a3 3 0 1 0 0 6h1V6a3 3 0 0 0-1-3Z"/><path d="M16 21a3 3 0 1 0 0-6h-1v3a3 3 0 0 0 1 3Z"/><path d="M8 21a3 3 0 1 0 0-6H6v3a3 3 0 0 0 2 3Z"/><path d="M16 3a3 3 0 1 0 0 6h2V6a3 3 0 0 0-2-3Z"/><path d="M9 9h6v6H9z"/>',
  close: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  "chevron-down": '<path d="m6 9 6 6 6-6"/>',
  "chevron-right": '<path d="m9 18 6-6-6-6"/>',
  "chevron-left": '<path d="m15 18-6-6 6-6"/>',
  "chevron-up-down": '<path d="m7 15 5 5 5-5"/><path d="m7 9 5-5 5 5"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
  filter: '<path d="M4 5h16"/><path d="M7 12h10"/><path d="M10 19h4"/>',
  sort: '<path d="M11 5h10"/><path d="M11 12h7"/><path d="M11 19h4"/><path d="m3 6 3-3 3 3"/><path d="M6 3v14"/>',
  kanban: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d="M15 4v10"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M9 10v10"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4"/><path d="M8 3v4"/><path d="M3 11h18"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.7-3 2.9-4.8 5.5-4.8s4.8 1.8 5.5 4.8"/><circle cx="17" cy="8.6" r="2.4"/><path d="M15.5 14.5c2.2.4 3.7 1.9 4.2 4.5"/>',
  shield: '<path d="M12 3 4.5 6v6c0 4.5 3 7.7 7.5 9 4.5-1.3 7.5-4.5 7.5-9V6L12 3Z"/>',
  "file-text": '<path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"/><path d="M14 3v5h5"/><path d="M9 13h6"/><path d="M9 17h6"/>',
  home: '<path d="m4 11 8-7 8 7"/><path d="M6 10v9a1 1 0 0 0 1 1h3v-6h4v6h3a1 1 0 0 0 1-1v-9"/>',
  inbox: '<path d="M4 12h4l2 3h4l2-3h4"/><path d="M4 12 5.5 5A1 1 0 0 1 6.5 4h11a1 1 0 0 1 1 1L20 12v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-6Z"/>',
  "bar-chart": '<path d="M4 20V10"/><path d="M12 20V4"/><path d="M20 20v-7"/><path d="M2 20h20"/>',
  settings:
    '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>',
  "log-out": '<path d="M9 21H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>',
  "more-horizontal": '<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>',
  phone: '<path d="M6.6 10.8a13 13 0 0 0 6.6 6.6l2.2-2.2a1.3 1.3 0 0 1 1.4-.3c1 .3 2 .5 3.1.5a1.3 1.3 0 0 1 1.3 1.3v3a1.3 1.3 0 0 1-1.3 1.3C10.6 21 3 13.4 3 4.3A1.3 1.3 0 0 1 4.3 3h3A1.3 1.3 0 0 1 8.6 4.3c0 1.1.2 2.1.5 3.1a1.3 1.3 0 0 1-.3 1.4L6.6 10.8Z"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 6 8.5 7 8.5-7"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
  "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.5 2.5L16 9.5"/>',
  "alert-triangle":
    '<path d="M12 3.5 21.5 20h-19L12 3.5Z"/><path d="M12 10v4"/><path d="M12 17.2v.1"/>',
  "alert-octagon":
    '<path d="M8 3h8l5 5v8l-5 5H8l-5-5V8l5-5Z"/><path d="M12 8v5"/><path d="M12 16.2v.1"/>',
  "arrow-right-left":
    '<path d="m17 3 4 4-4 4"/><path d="M3 7h18"/><path d="m7 21-4-4 4-4"/><path d="M21 17H3"/>',
  star: '<path d="m12 3 2.6 5.9 6.4.6-4.8 4.3 1.4 6.3L12 17l-5.6 3.1 1.4-6.3-4.8-4.3 6.4-.6L12 3Z"/>',
  bell: '<path d="M6 9a6 6 0 0 1 12 0c0 4 1.5 5.5 2 6H4c.5-.5 2-2 2-6Z"/><path d="M9.5 19a2.5 2.5 0 0 0 5 0"/>',
  sun: '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M4.9 4.9l1.5 1.5M17.6 17.6l1.5 1.5M2.5 12h2.2M19.3 12h2.2M4.9 19.1l1.5-1.5M17.6 6.4l1.5-1.5"/>',
  moon: '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z"/>',
  "layout-grid": '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/>',
  list: '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/>',
  download: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
  lock: '<rect x="4.5" y="10.5" width="15" height="10" rx="2"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/>',
  edit: '<path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3Z"/><path d="m14 6 4 4"/>',
  trash: '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/><path d="M10 11v6"/><path d="M14 11v6"/>',
  undo: '<path d="M7 10H3V6"/><path d="M3.5 15A8 8 0 1 0 6 6l-3 4"/>',
  loader: '<path d="M12 3v3"/><path d="M12 18v3"/><path d="m5.6 5.6 2 2"/><path d="m16.4 16.4 2 2"/><path d="M3 12h3"/><path d="M18 12h3"/><path d="m5.6 18.4 2-2"/><path d="m16.4 7.6 2-2"/>',
  "wifi-off": '<path d="m2 2 20 20"/><path d="M8.5 16.5a5 5 0 0 1 7 0"/><path d="M5 13a10 10 0 0 1 3-2.1"/><path d="M16 10.9A10 10 0 0 1 19 13"/><path d="M8.7 6.2A14.9 14.9 0 0 1 12 5.8c2 0 3.9.4 5.6 1.2"/><path d="M12 20h.01"/>',
  check: '<path d="m4 12 5 5L20 6"/>',
  "user-plus": '<circle cx="9" cy="8" r="4"/><path d="M2.5 20c.8-3.4 3.2-5.4 6.5-5.4s5.7 2 6.5 5.4"/><path d="M18 8h5"/><path d="M20.5 5.5v5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><path d="M12 7.6v.1"/>',
  spark: '<path d="M12 3v4"/><path d="M12 17v4"/><path d="M3 12h4"/><path d="M17 12h4"/><path d="m6 6 2.5 2.5"/><path d="m15.5 15.5 2.5 2.5"/><path d="m6 18 2.5-2.5"/><path d="m15.5 8.5 2.5-2.5"/>',
};

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, "ref"> {
  name: IconName;
  size?: number;
  title?: string;
}

/** Inline SVG icon. Purely decorative by default (aria-hidden); pass `title` for a meaningful icon-only control. */
export function Icon({ name, size = 16, title, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? "img" : "presentation"}
      aria-hidden={title ? undefined : true}
      focusable="false"
      {...rest}
      dangerouslySetInnerHTML={{ __html: (title ? `<title>${title}</title>` : "") + PATHS[name] }}
    />
  );
}
