import type { ColDef, GridOptions } from "ag-grid-community";

export const defaultColDef: ColDef = {
  sortable: true,
  resizable: true,
  minWidth: 60,
  initialFlex: 1,
  // Cells render their own content; inferring a data type would only change
  // how unrendered values and default filters behave.
  cellDataType: false,
  filterParams: { buttons: ["reset", "apply"] },
};

export const defaultGridOptions: GridOptions = {
  rowHeight: 28,
  headerHeight: 28,
  groupHeaderHeight: 28,
  animateRows: false,
  suppressCellFocus: true,
  // A column that arrives with the data (a new bucket) must not reset the
  // order the user arranged; the saved layout then places it.
  maintainColumnOrder: true,
  // Header titles show as the browser's own tooltip, like the rest of the UI.
  enableBrowserTooltips: true,
  columnTypes: {
    text: { filter: "agTextColumnFilter" },
    number: {
      cellClass: "ag-right-aligned-cell",
      headerClass: "ag-right-aligned-header",
      initialFlex: 0,
      initialWidth: 96,
    },
  },
};
