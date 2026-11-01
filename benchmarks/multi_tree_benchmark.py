"""
Reproducible Multi-Tree Scalability Benchmark for EmbeddedDB.

Measures:
- Database initialization time.
- Tree creation time (batched and per-tree sampling).
- Average and percentile (p50, p95, p99) tree-opening latency.
- Tree lookup latency (catalog has_tree / get_root).
- Record insertion latency (small payloads).
- Database reopening time (catalog loading).
- Listing all tree names time.
- Memory usage (RSS).
- On-disk database file sizes.
- Full integrity check time.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import platform
import resource
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from embeddeddb import EmbeddedDB


def get_rss_bytes() -> int:
    """Return resident set size in bytes."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() != "Darwin":
        rss *= 1024  # Linux ru_maxrss is in KiB
    return rss


def percentile(data: list[float], pct: float) -> float:
    """Calculate percentile from a sorted list of floats."""
    if not data:
        return 0.0
    k = (len(data) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return data[int(k)]
    d0 = data[int(f)] * (c - k)
    d1 = data[int(c)] * (k - f)
    return d0 + d1


def compute_latencies(durations: list[float]) -> dict[str, float]:
    """Compute summary statistics (in microseconds) for a list of latency measurements (in seconds)."""
    if not durations:
        return {}
    us_data = sorted(d * 1_000_000.0 for d in durations)
    return {
        "count": len(us_data),
        "mean_us": sum(us_data) / len(us_data),
        "p50_us": percentile(us_data, 50),
        "p95_us": percentile(us_data, 95),
        "p99_us": percentile(us_data, 99),
        "min_us": us_data[0],
        "max_us": us_data[-1],
    }


def run_benchmark_stage(
    tree_count: int,
    batch_size: int = 1000,
    insert_sample_size: int = 500,
    open_sample_size: int = 500,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Run a single benchmark stage for a given number of trees."""
    cleanup_needed = False
    if work_dir is None:
        temp_dir = tempfile.mkdtemp(prefix=f"objdb_bench_{tree_count}_")
        db_path = Path(temp_dir) / "bench.db"
        cleanup_needed = True
    else:
        db_path = work_dir / f"bench_{tree_count}.db"
        if db_path.exists():
            shutil.rmtree(db_path)

    results: dict[str, Any] = {"tree_count": tree_count}
    gc.collect()
    start_rss = get_rss_bytes()

    try:
        # 1. Database Initialization
        t0 = time.perf_counter()
        db = EmbeddedDB(db_path)
        db.open()
        t1 = time.perf_counter()
        results["init_time_s"] = t1 - t0

        # 2. Tree Creation
        # We create in batches of batch_size within explicit transactions
        # to ensure fair comparison across counts without drowning in 50k synchronous fsyncs.
        creation_t0 = time.perf_counter()
        created = 0
        while created < tree_count:
            cur_batch = min(batch_size, tree_count - created)
            with db.transaction():
                for i in range(cur_batch):
                    tree_name = f"tree_{created + i:08d}"
                    db.create_tree(tree_name)
            created += cur_batch
        creation_t1 = time.perf_counter()
        results["creation_total_time_s"] = creation_t1 - creation_t0
        results["creation_trees_per_sec"] = (
            tree_count / results["creation_total_time_s"]
            if results["creation_total_time_s"] > 0
            else float("inf")
        )

        # 3. Listing all trees
        t0 = time.perf_counter()
        all_tree_names = db.list_trees()
        t1 = time.perf_counter()
        results["list_trees_time_s"] = t1 - t0
        assert len(all_tree_names) == tree_count, f"Mismatch: {len(all_tree_names)} != {tree_count}"

        # 4. Record Insertion into sample of trees
        # Choose a representative sample of trees (or all if tree_count <= insert_sample_size)
        sample_count = min(tree_count, insert_sample_size)
        # Evenly spaced sample indices
        step = max(1, tree_count // sample_count)
        sampled_indices = [i * step for i in range(sample_count) if i * step < tree_count]

        insert_durations: list[float] = []
        with db.transaction():
            for idx in sampled_indices:
                t_name = f"tree_{idx:08d}"
                t_obj = db.open_tree(t_name)
                it0 = time.perf_counter()
                t_obj.put(1, b"test_payload_alpha")
                t_obj.put(2, b"test_payload_beta")
                t_obj.put(3, b"test_payload_gamma")
                it1 = time.perf_counter()
                insert_durations.append(it1 - it0)

        results["insert_sample_count"] = len(sampled_indices)
        results["insert_latency_stats"] = compute_latencies(insert_durations)

        # 5. Measure on-disk file sizes before close
        data_file = db_path / "objectdb.data"
        wal_file = db_path / "objectdb.wal"
        seg_file = db_path / "segment-0.dat"

        results["data_file_size_bytes"] = data_file.stat().st_size if data_file.exists() else 0
        results["wal_file_size_bytes"] = wal_file.stat().st_size if wal_file.exists() else 0
        results["seg_file_size_bytes"] = seg_file.stat().st_size if seg_file.exists() else 0

        # Close database
        db.close()
        del db
        gc.collect()

        # 6. Database Reopening Time (Cold Catalog Load)
        t0 = time.perf_counter()
        db = EmbeddedDB(db_path)
        db.open()
        t1 = time.perf_counter()
        results["reopen_time_s"] = t1 - t0
        results["catalog_pages_count"] = len(db.catalog.get_catalog_pages())

        # 7. Catalog Lookup Latencies (has_tree & get_root)
        lookup_sample_count = min(tree_count, open_sample_size)
        step = max(1, tree_count // lookup_sample_count)
        lookup_indices = [i * step for i in range(lookup_sample_count) if i * step < tree_count]

        lookup_durations: list[float] = []
        for idx in lookup_indices:
            t_name = f"tree_{idx:08d}"
            lt0 = time.perf_counter()
            _ = db.catalog.has_tree(t_name)
            _ = db.catalog.get_root(t_name)
            lt1 = time.perf_counter()
            lookup_durations.append(lt1 - lt0)

        results["lookup_sample_count"] = len(lookup_indices)
        results["lookup_latency_stats"] = compute_latencies(lookup_durations)

        # 8. Tree Opening Latencies (fresh Tree handle creation from cold open)
        open_durations: list[float] = []
        for idx in lookup_indices:
            t_name = f"tree_{idx:08d}"
            ot0 = time.perf_counter()
            tree_h = db.open_tree(t_name)
            ot1 = time.perf_counter()
            open_durations.append(ot1 - ot0)

        results["open_sample_count"] = len(lookup_indices)
        results["open_latency_stats"] = compute_latencies(open_durations)

        # 9. Verify read of inserted records
        for idx in sampled_indices[:min(50, len(sampled_indices))]:
            t_name = f"tree_{idx:08d}"
            t_h = db.open_tree(t_name)
            assert t_h.get(1) == b"test_payload_alpha"
            assert t_h.get(2) == b"test_payload_beta"
            assert t_h.get(3) == b"test_payload_gamma"

        # 10. Memory Usage
        gc.collect()
        end_rss = get_rss_bytes()
        results["peak_rss_mb"] = end_rss / (1024 * 1024)
        results["rss_growth_mb"] = (end_rss - start_rss) / (1024 * 1024)

        # 11. Integrity Check (for up to 10k trees by default, or run if fast)
        if tree_count <= 10000:
            it0 = time.perf_counter()
            integ = db.check_integrity(raise_on_error=True)
            it1 = time.perf_counter()
            results["integrity_check_time_s"] = it1 - it0
            results["integrity_valid"] = integ["valid"]
            results["total_pages"] = integ["total_pages"]
            results["reachable_pages"] = integ["reachable_pages"]
        else:
            results["integrity_check_time_s"] = "skipped_for_speed"

        db.close()

    finally:
        if cleanup_needed and db_path.parent.exists():
            shutil.rmtree(db_path.parent, ignore_errors=True)

    return results


def main():
    parser = argparse.ArgumentParser(description="EmbeddedDB Multi-Tree Benchmark")
    parser.add_argument(
        "--stages",
        nargs="+",
        type=int,
        default=[100, 1000, 10000, 50000],
        help="List of tree counts to test",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Batch size for tree creation transactions",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output JSON file path",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("EmbeddedDB Multi-Tree Scalability Benchmark")
    print(f"Platform: {platform.system()} {platform.machine()} | Python {platform.python_version()}")
    print("=" * 70)

    all_results = []
    for count in args.stages:
        print(f"\n---> Running Benchmark Stage: {count:,} trees ...")
        t_start = time.time()
        try:
            res = run_benchmark_stage(count, batch_size=args.batch_size)
            elapsed = time.time() - t_start
            all_results.append(res)

            print(f"  Stage {count:,} completed in {elapsed:.2f}s:")
            print(f"    - Creation Total Time:  {res['creation_total_time_s']:.3f}s ({res['creation_trees_per_sec']:.1f} trees/sec)")
            print(f"    - Reopen (Catalog Load):{res['reopen_time_s'] * 1000.0:.2f}ms ({res['catalog_pages_count']} catalog pages)")
            print(f"    - Tree Listing Time:    {res['list_trees_time_s'] * 1000.0:.2f}ms")
            print(f"    - Open Latency (mean):  {res['open_latency_stats']['mean_us']:.2f} us (p95: {res['open_latency_stats']['p95_us']:.2f} us)")
            print(f"    - Lookup Latency (mean):{res['lookup_latency_stats']['mean_us']:.2f} us (p95: {res['lookup_latency_stats']['p95_us']:.2f} us)")
            print(f"    - Insert 3 keys (mean): {res['insert_latency_stats']['mean_us']:.2f} us/tree")
            print(f"    - Data File Size:       {res['data_file_size_bytes'] / (1024 * 1024):.2f} MB")
            print(f"    - Peak RSS:             {res['peak_rss_mb']:.2f} MB")
            if "integrity_check_time_s" in res and isinstance(res["integrity_check_time_s"], float):
                print(f"    - Integrity Check Time: {res['integrity_check_time_s']:.2f}s")
        except Exception as e:
            print(f"  Stage {count:,} FAILED or halted: {e}")
            import traceback
            traceback.print_exc()
            break

    if args.out:
        with open(args.out, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\nWrote full benchmark results to {args.out}")


if __name__ == "__main__":
    main()
