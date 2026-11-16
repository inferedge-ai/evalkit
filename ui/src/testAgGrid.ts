import { fireEvent } from "@testing-library/react";

// An open AG Grid popup (a column filter or menu) keeps a timer that can fire
// after a test file's jsdom is gone and throw there. Escape closes it while
// jsdom is still up; call this before unmounting.
export function closeGridPopups() {
  if (typeof document === "undefined") return;
  for (const popup of document.querySelectorAll(".ag-popup-child"))
    fireEvent.keyDown(popup, { key: "Escape" });
}
