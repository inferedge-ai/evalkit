import type { ReactNode } from "react";
import { DialogFooter } from "@/components/ui/dialog";

// The footer stays in view while the fields scroll, so a form's errors go
// here, above its buttons.
export function FormDialogFooter({
  errors,
  children,
}: {
  errors: (string | null | undefined)[];
  children: ReactNode;
}) {
  const shown = errors.filter((message): message is string => !!message);
  return (
    <DialogFooter className="flex-col sm:flex-col">
      {shown.length > 0 && (
        <div
          role="alert"
          className="flex max-h-24 flex-col gap-1 overflow-y-auto text-meta text-err-text"
        >
          {shown.map((message) => (
            <div key={message}>{message}</div>
          ))}
        </div>
      )}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        {children}
      </div>
    </DialogFooter>
  );
}
