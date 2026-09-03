import { createContext, useContext } from "react";

export type ToastTone = "success" | "info" | "danger";

export interface ToastItem {
  id: number;
  tone: ToastTone;
  message: string;
}

export interface ToastContextValue {
  pushToast: (tone: ToastTone, message: string) => void;
  dismissToast: (id: number) => void;
}

export const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within ToastProvider");
  }
  return context;
}
