import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { AgGridReact } from "ag-grid-react";
import type {
  AgGridReactProps,
  CustomCellRendererProps,
  CustomNoRowsOverlayProps,
} from "ag-grid-react";
import {
  CellStyleModule,
  ClientSideRowModelApiModule,
  ClientSideRowModelModule,
  ColumnApiModule,
  ColumnAutoSizeModule,
  ModuleRegistry,
  PinnedRowModule,
  ScrollApiModule,
  TextFilterModule,
  TooltipModule,
  ValidationModule,
} from "ag-grid-community";
import type {
  ColDef,
  ColGroupDef,
  Column,
  ColumnState,
  GetRowIdParams,
  GridApi,
  OverlayType,
  ValueGetterParams,
} from "ag-grid-community";
import {
  loadColumnState,
  mergeColumnState,
  orderColumnState,
  saveColumnState,
} from "./aggrid/columnState";
import { defaultColDef, defaultGridOptions } from "./aggrid/defaults";
import { gridTheme } from "./aggrid/theme";

ModuleRegistry.registerModules([
  CellStyleModule,
  ClientSideRowModelApiModule,
  ClientSideRowModelModule,
  ColumnApiModule,
  ColumnAutoSizeModule,
  PinnedRowModule,
  ScrollApiModule,
  TextFilterModule,
  TooltipModule,
  ...(import.meta.env.DEV ? [ValidationModule] : []),
]);

export type GridColumn<T> = ColDef<T> | ColGroupDef<T>;

export function renderCell<T>(render: (row: T) => ReactNode) {
  return ({ data }: CustomCellRendererProps<T>) => (data ? render(data) : null);
}

export function readValue<T, V>(read: (row: T) => V) {
  return ({ data }: ValueGetterParams<T>) => (data ? read(data) : undefined);
}

// Rows without a value sort after the rest in either direction.
export function compareNullsLast(
  a: number | null | undefined,
  b: number | null | undefined,
  _nodeA: unknown,
  _nodeB: unknown,
  descending: boolean,
): number {
  if (a == null || b == null)
    return a == null && b == null ? 0 : (a == null) !== descending ? 1 : -1;
  return a - b;
}

type ColumnEvents<T> = Required<
  Pick<
    AgGridReactProps<T>,
    | "onGridReady"
    | "onGridPreDestroyed"
    | "onNewColumnsLoaded"
    | "onSortChanged"
    | "onColumnMoved"
    | "onColumnVisible"
    | "onColumnResized"
    | "onColumnPinned"
    | "onModelUpdated"
  >
>;

export interface DataGridHandle<T> {
  api: GridApi<T> | null;
  // The rows the column filters leave, or null while no column filter is set.
  filteredRows: T[] | null;
  reset: () => void;
  events: ColumnEvents<T>;
}

function getColumnIds<T>(api: GridApi<T>): string[] {
  return (api.getColumns() ?? []).map((c) => c.getColId());
}

// One table's grid. Its column layout (order, hidden, sort, widths) is saved
// under `tableId` and restored whenever the grid is given its columns.
export function useDataGrid<T>(tableId: string): DataGridHandle<T> {
  const [api, setApi] = useState<GridApi<T> | null>(null);
  // Re-renders the caller's column toggles when the layout changes.
  const [, setLayout] = useState("");
  const [filteredRows, setFilteredRows] = useState<T[] | null>(null);
  const saved = useRef<ColumnState[] | null>(null);
  const restoredColumns = useRef("");

  return useMemo(() => {
    const loadLayout = () => (saved.current ??= loadColumnState(tableId));
    const trackLayout = (grid: GridApi<T>) =>
      setLayout(
        grid
          .getAllGridColumns()
          .map((c) => `${c.isVisible() ? "" : "-"}${c.getColId()}`)
          .join(),
      );
    const restoreLayout = ({ api: grid }: { api: GridApi<T> }) => {
      const ids = getColumnIds(grid);
      // Rebuilt definitions of the same columns change nothing; restoring
      // then would undo a change the page made since the last save.
      if (JSON.stringify(ids) === restoredColumns.current) return;
      restoredColumns.current = JSON.stringify(ids);
      grid.applyColumnState({
        state: orderColumnState(loadLayout(), ids),
        applyOrder: true,
      });
      trackLayout(grid);
    };
    const saveLayout = ({
      api: grid,
      finished,
    }: {
      api: GridApi<T>;
      finished?: boolean;
    }) => {
      trackLayout(grid);
      if (finished === false) return;
      // New columns resize the others before they are restored; saving then
      // would store the grid's placement of them (last) over the default one.
      if (JSON.stringify(getColumnIds(grid)) !== restoredColumns.current)
        return;
      saved.current = mergeColumnState(loadLayout(), grid.getColumnState());
      saveColumnState(tableId, saved.current);
    };
    const collectFilteredRows = ({ api: grid }: { api: GridApi<T> }) => {
      if (!grid.isAnyFilterPresent()) return setFilteredRows(null);
      const rows: T[] = [];
      grid.forEachNodeAfterFilter(({ data }) => data && rows.push(data));
      setFilteredRows((prev) =>
        prev?.length === rows.length && prev.every((r, i) => r === rows[i])
          ? prev
          : rows,
      );
    };
    return {
      api,
      filteredRows,
      reset: () => {
        saved.current = [];
        saveColumnState(tableId, []);
        api?.resetColumnState();
      },
      events: {
        onGridReady: (e) => setApi(e.api),
        onGridPreDestroyed: () => {
          restoredColumns.current = "";
          setApi(null);
          setFilteredRows(null);
        },
        onNewColumnsLoaded: restoreLayout,
        onSortChanged: saveLayout,
        onColumnMoved: saveLayout,
        onColumnVisible: saveLayout,
        onColumnResized: saveLayout,
        onColumnPinned: saveLayout,
        onModelUpdated: collectFilteredRows,
      },
    };
  }, [tableId, api, filteredRows]);
}

function EmptyRows({
  message,
}: CustomNoRowsOverlayProps & { message: ReactNode }) {
  return <div className="pane-status">{message}</div>;
}

const NO_ROWS_OVERLAY: OverlayType[] = ["noRows"];

export function DataGrid<T>({
  grid,
  rows,
  columns,
  getRowId,
  loading = false,
  empty,
  height,
  pinnedBottomRowData,
  getRowHeight,
  autoSizeStrategy,
}: {
  grid: DataGridHandle<T>;
  rows: T[];
  columns: GridColumn<T>[];
  getRowId: (row: T) => string;
  loading?: boolean;
  empty?: ReactNode;
  // A fixed height scrolls the rows under a pinned header; without one the
  // grid grows with its rows.
  height?: string;
} & Pick<
  AgGridReactProps<T>,
  "pinnedBottomRowData" | "getRowHeight" | "autoSizeStrategy"
>) {
  // AG Grid reads getRowId once: pass a function that doesn't change.
  const rowId = useCallback(
    ({ data }: GetRowIdParams<T>) => getRowId(data),
    [getRowId],
  );

  return (
    <div style={height ? { height } : undefined}>
      <AgGridReact<T>
        {...defaultGridOptions}
        {...grid.events}
        theme={gridTheme}
        defaultColDef={defaultColDef}
        rowData={rows}
        columnDefs={columns}
        getRowId={rowId}
        loading={loading}
        domLayout={height ? "normal" : "autoHeight"}
        noRowsOverlayComponent={EmptyRows}
        noRowsOverlayComponentParams={{ message: empty }}
        suppressOverlays={empty === undefined ? NO_ROWS_OVERLAY : undefined}
        pinnedBottomRowData={pinnedBottomRowData}
        getRowHeight={getRowHeight}
        autoSizeStrategy={autoSizeStrategy}
      />
    </div>
  );
}

// The group a column must stay inside: the grid refuses a move that would split
// a group it keeps together.
function getMarriedGroup(column: Column) {
  const parent = column.getParent();
  return parent?.getColGroupDef()?.marryChildren ? parent : null;
}

function canSwapColumns(column: Column, other: Column | undefined) {
  return !!other && getMarriedGroup(column) === getMarriedGroup(other);
}

export function ColumnsMenu<T>({ grid }: { grid: DataGridHandle<T> }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const api = grid.api;
  const all = api?.getAllGridColumns() ?? [];
  const movable = all.filter((c) => !c.getColDef().lockVisible);

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        className="btn"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        Columns
      </button>
      {open && api && (
        <div className="select-menu columns-menu right-0 left-auto">
          <ul className="select-list">
            {movable.map((column, i) => {
              const id = column.getColId();
              const name = api.getDisplayNameForColumn(column, "header");
              return (
                <li key={id} className="columns-menu-row">
                  <label className="flex flex-1 items-center gap-2">
                    <input
                      type="checkbox"
                      checked={column.isVisible()}
                      onChange={(e) =>
                        api.setColumnsVisible([column], e.target.checked)
                      }
                    />
                    {name}
                  </label>
                  <button
                    type="button"
                    className="btn btn-sm btn-quiet"
                    aria-label={`move ${name} up`}
                    disabled={!canSwapColumns(column, movable[i - 1])}
                    onClick={() =>
                      api.moveColumns([column], all.indexOf(movable[i - 1]))
                    }
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-quiet"
                    aria-label={`move ${name} down`}
                    disabled={!canSwapColumns(column, movable[i + 1])}
                    onClick={() =>
                      api.moveColumns([column], all.indexOf(movable[i + 1]))
                    }
                  >
                    ↓
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="flex justify-end border-t border-line p-1.5">
            <button type="button" className="btn btn-sm" onClick={grid.reset}>
              Reset
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
