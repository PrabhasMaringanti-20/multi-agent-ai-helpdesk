import { create } from "zustand";

export type ToastKind = "success" | "error" | "info";

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

interface UiState {
  toasts: Toast[];
  pushToast: (kind: ToastKind, message: string) => void;
  dismissToast: (id: number) => void;
}

let toastId = 0;

export const useUiStore = create<UiState>((set) => ({
  toasts: [],
  pushToast: (kind, message) =>
    set((state) => ({ toasts: [...state.toasts, { id: ++toastId, kind, message }] })),
  dismissToast: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
}));

export const toast = {
  success: (message: string) => useUiStore.getState().pushToast("success", message),
  error: (message: string) => useUiStore.getState().pushToast("error", message),
  info: (message: string) => useUiStore.getState().pushToast("info", message),
};
