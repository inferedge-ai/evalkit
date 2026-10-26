from evalkit.core.costmap import bundled_cost_map_path, fetch_cost_map, write_cost_map

if __name__ == "__main__":
    dest = bundled_cost_map_path()
    write_cost_map(fetch_cost_map(), dest)
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")
