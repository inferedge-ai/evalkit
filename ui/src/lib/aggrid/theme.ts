import { themeQuartz } from "ag-grid-community";

// Colours are CSS variables, so the dark theme applies without a second theme.
export const gridTheme = themeQuartz.withParams({
  backgroundColor: "var(--card)",
  foregroundColor: "var(--foreground)",
  borderColor: "var(--border)",
  borderRadius: 8,
  borderWidth: 1,
  spacing: 4,
  fontSize: 14,
  fontFamily: "var(--font-sans)",

  headerBackgroundColor: "var(--card)",
  headerTextColor: "var(--muted-foreground)",
  headerFontSize: 11,
  headerFontWeight: 600,

  rowHoverColor: "color-mix(in oklab, var(--primary) 8%, transparent)",
  selectedRowBackgroundColor:
    "color-mix(in oklab, var(--primary) 14%, transparent)",
  oddRowBackgroundColor: "var(--card)",

  accentColor: "var(--primary)",

  iconSize: 14,
  columnBorder: true,

  inputBackgroundColor: "var(--card)",
  inputBorderRadius: 6,
  listItemHeight: 32,

  popupShadow:
    "0 20px 48px -12px rgba(0,0,0,0.20), 0 8px 16px -4px rgba(0,0,0,0.12)",
  menuBackgroundColor: "var(--card)",
  menuBorder: "1px solid var(--border)",
  menuTextColor: "var(--foreground)",
});
