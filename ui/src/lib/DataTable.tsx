import { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import type {
  MouseEvent as ReactMouseEvent,
  ReactNode,
  TouchEvent as ReactTouchEvent,
} from "react";
import {
  columnOrderingFeature,
  columnResizingFeature,
  columnSizingFeature,
  columnVisibilityFeature,
  createColumnHelper,
  createSortedRowModel,
  flexRender,
  metaHelper,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_basic,
  sortFn_datetime,
  sortFn_text,
  tableFeatures,
  useTable,
} from "@tanstack/react-table";
import type {
  ColumnDef,
  Header,
  ColumnOrderState,
  ColumnSizingState,
  ReactTable,
  SortingState,
  ColumnVisibilityState as VisibilityState,
} from "@tanstack/react-table";
import { mergeOrder, moveColumn } from "./tableState";

export interface ColumnLook {
  align?: "right";
  title?: string;
  label?: string;
  wrap?: boolean;
  className?: string;
  headerClassName?: string;
  cellClass?: (row: any) => string;
}

const features = tableFeatures({
  columnOrderingFeature,
  columnVisibilityFeature,
  columnSizingFeature,
  columnResizingFeature,
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: {
    alphanumeric: sortFn_alphanumeric,
    basic: sortFn_basic,
    datetime: sortFn_datetime,
    text: sortFn_text,
  },
  columnMeta: metaHelper<ColumnLook>(),
});

type Features = typeof features;
export type Column<T extends object> = ColumnDef<Features, T, any>;
type Table<T extends object> = ReactTable<Features, T>;

export function columnHelper<T extends object>() {
  return createColumnHelper<Features, T>();
}

interface SavedState {
  sorting: SortingState;
  order: ColumnOrderState;
  hidden: VisibilityState;
  sizing: ColumnSizingState;
}

const STORAGE_PREFIX = "evalkit.table.";

function loadState(id: string): Partial<SavedState> {
  const raw = localStorage.getItem(STORAGE_PREFIX + id);
  return raw ? (JSON.parse(raw) as Partial<SavedState>) : {};
}

export function useDataTable<T extends object>({
  id,
  data,
  columns,
  getRowId,
  defaultHidden = [],
}: {
  id: string;
  data: T[];
  columns: Column<T>[];
  getRowId: (row: T) => string;
  defaultHidden?: string[];
}): Table<T> {
  const saved = useMemo(() => loadState(id), [id]);
  const [sorting, setSorting] = useState<SortingState>(saved.sorting ?? []);
  const [order, setOrder] = useState<ColumnOrderState>(saved.order ?? []);
  const [hidden, setHidden] = useState<VisibilityState>({
    ...Object.fromEntries(defaultHidden.map((c) => [c, false])),
    ...saved.hidden,
  });
  const [sizing, setSizing] = useState<ColumnSizingState>(saved.sizing ?? {});

  useEffect(() => {
    const state: SavedState = { sorting, order, hidden, sizing };
    localStorage.setItem(STORAGE_PREFIX + id, JSON.stringify(state));
  }, [id, sorting, order, hidden, sizing]);

  const ids = leafIds(columns);
  const columnOrder = mergeOrder(order, ids);

  return useTable({
    features,
    data,
    columns,
    getRowId,
    state: {
      sorting,
      columnOrder,
      columnVisibility: hidden,
      columnSizing: sizing,
    },
    onSortingChange: setSorting,
    onColumnOrderChange: setOrder,
    onColumnVisibilityChange: setHidden,
    onColumnSizingChange: setSizing,
    enableSortingRemoval: true,
    columnResizeMode: "onChange",
    defaultColumn: { minSize: 48 },
  });
}

function leafIds<T extends object>(
  columns: readonly ColumnDef<Features, T, any>[],
): string[] {
  return columns.flatMap((c) =>
    "columns" in c && c.columns ? leafIds(c.columns) : [c.id ?? ""],
  );
}

function reset<T extends object>(table: Table<T>, defaultHidden: string[]) {
  table.setSorting([]);
  table.setColumnOrder([]);
  table.setColumnSizing({});
  table.setColumnVisibility(
    Object.fromEntries(defaultHidden.map((c) => [c, false])),
  );
}

function headerText<T extends object>(
  table: Table<T>,
  columnId: string,
): string {
  const def = table.getColumn(columnId)?.columnDef;
  if (def?.meta?.label) return def.meta.label;
  return typeof def?.header === "string" ? def.header : columnId;
}

export function ColumnsMenu<T extends object>({
  table,
  defaultHidden = [],
}: {
  table: Table<T>;
  defaultHidden?: string[];
}) {
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

  const order = table.state.columnOrder;
  const movable = table
    .getAllLeafColumns()
    .filter((c) => c.getCanHide())
    .map((c) => c.id);

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
      {open && (
        <div className="select-menu columns-menu right-0 left-auto">
          <ul className="select-list">
            {order
              .filter((columnId) => movable.includes(columnId))
              .map((columnId, i, shown) => {
                const column = table.getColumn(columnId);
                if (!column) return null;
                return (
                  <li key={columnId} className="columns-menu-row">
                    <label className="flex flex-1 items-center gap-2">
                      <input
                        type="checkbox"
                        checked={column.getIsVisible()}
                        onChange={column.getToggleVisibilityHandler()}
                      />
                      {headerText(table, columnId)}
                    </label>
                    <button
                      type="button"
                      className="btn btn-sm btn-quiet"
                      aria-label={`move ${columnId} up`}
                      disabled={i === 0}
                      onClick={() =>
                        table.setColumnOrder(
                          moveColumn(order, columnId, shown[i - 1]),
                        )
                      }
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-quiet"
                      aria-label={`move ${columnId} down`}
                      disabled={i === shown.length - 1}
                      onClick={() =>
                        table.setColumnOrder(
                          moveColumn(order, columnId, shown[i + 1]),
                        )
                      }
                    >
                      ↓
                    </button>
                  </li>
                );
              })}
          </ul>
          <div className="flex justify-end border-t border-line p-1.5">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => reset(table, defaultHidden)}
            >
              Reset
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// Columns without a stored size take their width from the content, so the
// library's resize start (header.getSize()) would be its 150px default. Store
// the rendered width first; flushSync makes the handler read it.
function startResize<T extends object>(
  e: ReactMouseEvent<HTMLElement> | ReactTouchEvent<HTMLElement>,
  table: Table<T>,
  header: Header<Features, T, unknown>,
) {
  e.stopPropagation();
  const id = header.column.id;
  const rendered = e.currentTarget.parentElement?.offsetWidth;
  if (!(id in table.state.columnSizing) && rendered)
    flushSync(() =>
      table.setColumnSizing((sizes) => ({ ...sizes, [id]: rendered })),
    );
  header.getResizeHandler()(e);
}

function sortGlyph(dir: false | "asc" | "desc"): string {
  if (dir === "asc") return " ↑";
  if (dir === "desc") return " ↓";
  return "";
}

export function DataTable<T extends object>({
  table,
  loading = false,
  skeletonRows = 8,
  empty,
  className = "",
  containerClassName = "card overflow-x-auto",
}: {
  table: Table<T>;
  loading?: boolean;
  skeletonRows?: number;
  empty?: ReactNode;
  className?: string;
  containerClassName?: string;
}) {
  const sizing = table.state.columnSizing;
  const width = (columnId: string, size: number) =>
    columnId in sizing
      ? { width: size, minWidth: size, maxWidth: size }
      : undefined;
  const visible = table.getVisibleLeafColumns();
  const rows = table.getRowModel().rows;
  const footers = visible.some((c) => c.columnDef.footer)
    ? table.getFooterGroups()[0]
    : null;

  return (
    <div className={containerClassName}>
      <table className={`data-table grid-table ${className}`}>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const column = header.column;
                const meta = column.columnDef.meta;
                const sortable = column.getCanSort();
                if (header.isPlaceholder)
                  return (
                    <th
                      key={header.id}
                      colSpan={header.colSpan}
                      className={meta?.headerClassName}
                    />
                  );
                return (
                  <th
                    key={header.id}
                    colSpan={header.colSpan}
                    className={`${meta?.align === "right" ? "col-num" : ""} ${
                      meta?.headerClassName ?? ""
                    }`}
                    style={width(column.id, header.getSize())}
                    title={meta?.title}
                    aria-sort={
                      column.getIsSorted() === "asc"
                        ? "ascending"
                        : column.getIsSorted() === "desc"
                          ? "descending"
                          : undefined
                    }
                  >
                    <span
                      className={sortable ? "th-sortable" : undefined}
                      onClick={
                        sortable ? column.getToggleSortingHandler() : undefined
                      }
                    >
                      {flexRender(column.columnDef.header, header.getContext())}
                      {sortGlyph(column.getIsSorted())}
                    </span>
                    {column.getCanResize() && !header.subHeaders.length && (
                      <span
                        className={`th-resizer ${column.getIsResizing() ? "active" : ""}`}
                        role="separator"
                        aria-label={`resize ${column.id}`}
                        onMouseDown={(e) => startResize(e, table, header)}
                        onTouchStart={(e) => startResize(e, table, header)}
                        onDoubleClick={() => column.resetSize()}
                      />
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {loading &&
            Array.from({ length: skeletonRows }, (_, i) => (
              <tr key={i}>
                <td colSpan={visible.length}>
                  <span
                    className="skeleton block h-4"
                    style={{ width: `${25 + ((i * 7) % 60)}%` }}
                  />
                </td>
              </tr>
            ))}
          {!loading &&
            rows.map((row) => (
              <tr key={row.id}>
                {row.getVisibleCells().map((cell) => {
                  const meta = cell.column.columnDef.meta;
                  return (
                    <td
                      key={cell.id}
                      className={`${meta?.align === "right" ? "col-num" : ""} ${
                        meta?.wrap ? "cell-wrap" : ""
                      } ${cell.column.id in sizing ? "cell-sized" : ""} ${
                        meta?.className ?? ""
                      } ${meta?.cellClass?.(row.original) ?? ""}`}
                      style={width(cell.column.id, cell.column.getSize())}
                    >
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext(),
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          {!loading && rows.length === 0 && empty && (
            <tr>
              <td colSpan={visible.length} className="pane-status">
                {empty}
              </td>
            </tr>
          )}
        </tbody>
        {footers && !loading && (
          <tfoot>
            <tr>
              {footers.headers.map((header) => {
                const meta = header.column.columnDef.meta;
                return (
                  <td
                    key={header.id}
                    className={`${meta?.align === "right" ? "col-num" : ""} ${
                      meta?.className ?? ""
                    }`}
                    style={width(header.column.id, header.getSize())}
                  >
                    {flexRender(
                      header.column.columnDef.footer,
                      header.getContext(),
                    )}
                  </td>
                );
              })}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
