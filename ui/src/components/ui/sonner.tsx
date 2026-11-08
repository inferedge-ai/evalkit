import * as React from "react";
import { Toast } from "@base-ui/react/toast";
import {
  CircleCheckIcon,
  InfoIcon,
  TriangleAlertIcon,
  OctagonXIcon,
  Loader2Icon,
  XIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert";

// Module scope, so `toast.*` works outside React too (event handlers, mutation
// callbacks).
const toastManager = Toast.createToastManager();

type ToastKind = "success" | "error" | "info" | "warning" | "loading";

export type ToastOptions = {
  description?: React.ReactNode;
  action?: { label: React.ReactNode; onClick?: () => void };
  /** ms; `Infinity` disables auto-dismiss. */
  duration?: number;
  /** ms before auto-dismiss; 0 disables. @default 5000 */
  timeout?: number;
  /** Re-using an id updates that toast in place. */
  id?: string;
  priority?: "low" | "high";
  onClose?: () => void;
};

function emit(type: ToastKind, title: React.ReactNode, opts?: ToastOptions) {
  const { action, duration, timeout, ...rest } = opts ?? {};
  const resolvedTimeout =
    duration !== undefined
      ? Number.isFinite(duration)
        ? duration
        : 0
      : timeout;
  return toastManager.add({
    title,
    type,
    ...rest,
    ...(resolvedTimeout !== undefined ? { timeout: resolvedTimeout } : {}),
    ...(action
      ? { actionProps: { children: action.label, onClick: action.onClick } }
      : {}),
  });
}

type ToastFn = ((title: React.ReactNode, opts?: ToastOptions) => string) & {
  success: (title: React.ReactNode, opts?: ToastOptions) => string;
  error: (title: React.ReactNode, opts?: ToastOptions) => string;
  info: (title: React.ReactNode, opts?: ToastOptions) => string;
  warning: (title: React.ReactNode, opts?: ToastOptions) => string;
  loading: (title: React.ReactNode, opts?: ToastOptions) => string;
  dismiss: (id?: string) => void;
};

const toastBase = ((title: React.ReactNode, opts?: ToastOptions) =>
  emit("info", title, opts)) as ToastFn;

toastBase.success = (title, opts) => emit("success", title, opts);
toastBase.error = (title, opts) => emit("error", title, opts);
toastBase.info = (title, opts) => emit("info", title, opts);
toastBase.warning = (title, opts) => emit("warning", title, opts);
toastBase.loading = (title, opts) => emit("loading", title, opts);
toastBase.dismiss = (id) => toastManager.close(id);

export const toast = toastBase;

const ICON_BY_TYPE: Record<string, React.ReactNode> = {
  success: <CircleCheckIcon className="size-4 text-current" />,
  error: <OctagonXIcon className="size-4 text-current" />,
  warning: <TriangleAlertIcon className="size-4 text-current" />,
  info: <InfoIcon className="size-4 text-current" />,
  loading: <Loader2Icon className="size-4 animate-spin text-current" />,
};

function ToastList() {
  const { toasts } = Toast.useToastManager();
  return toasts.map((t) => {
    const type = t.type ?? "info";
    return (
      <Toast.Root
        key={t.id}
        toast={t}
        className={cn(
          "group pointer-events-auto w-[var(--toast-width,360px)] max-w-[calc(100vw-2rem)]",
          "transition-all duration-200 data-[ending]:opacity-0 data-[starting]:opacity-0",
        )}
      >
        <Alert className="shadow-lg">
          {ICON_BY_TYPE[type] ?? ICON_BY_TYPE.info}
          {t.title ? (
            <AlertTitle>
              <Toast.Title />
            </AlertTitle>
          ) : null}
          {t.description ? (
            <AlertDescription>
              <Toast.Description />
            </AlertDescription>
          ) : null}
          {t.actionProps ? (
            <Toast.Action className="col-start-2 mt-1.5 inline-flex h-7 w-fit items-center justify-center rounded-md border bg-transparent px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-foreground/5 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none" />
          ) : null}
          <AlertAction>
            <Toast.Close
              aria-label="Close"
              className="inline-flex size-5 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground"
            >
              <XIcon className="size-3.5" />
            </Toast.Close>
          </AlertAction>
        </Alert>
      </Toast.Root>
    );
  });
}

function Toaster() {
  return (
    <Toast.Provider toastManager={toastManager}>
      <Toast.Portal>
        <Toast.Viewport className="fixed top-4 right-4 z-[100] flex w-[var(--toast-width,360px)] max-w-[calc(100vw-2rem)] flex-col gap-2 outline-none">
          <ToastList />
        </Toast.Viewport>
      </Toast.Portal>
    </Toast.Provider>
  );
}

export { Toaster };
