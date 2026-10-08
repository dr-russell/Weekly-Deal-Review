import argparse
import json

from . import store
from .pipeline import import_history, run_job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["import", "prepare", "resume", "enrich", "rates", "reconcile", "review", "status", "repair-sources", "normalize-geography"])
    parser.add_argument("--week")
    parser.add_argument("--target", choices=["test", "local", "production"], default="test")
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Apply the repair or normalization; default is a dry run")
    parser.add_argument("--company", action="append", help="Exact company name to refresh; repeat for multiple companies")
    parser.add_argument("--from-run", help="Failed preparation run to recover without repeating completed research")
    args = parser.parse_args()
    store.initialize()
    if args.action == "import":
        print(json.dumps(import_history()))
    elif args.action == "status":
        print(json.dumps(store.query("SELECT id,status,stage,elapsed,error FROM runs ORDER BY started_at DESC LIMIT 5"), indent=2))
    elif args.action == "normalize-geography":
        from .geography_migration import normalize_database
        print(json.dumps(normalize_database(args.week, args.apply), indent=2))
    else:
        if not args.week:
            parser.error("--week is required")
        if args.action == "repair-sources":
            from .source_integrity import rebuild_sources
            print(json.dumps(rebuild_sources(args.week, args.apply), indent=2))
            return
        run_id = store.new_run(args.week, args.action, args.target)
        print(json.dumps({"run_id": run_id}), flush=True)
        run_job(run_id, args.week, args.action, args.target, args.collect, args.company, args.from_run)
        result = store.get_run(run_id)
        print(json.dumps({k: result[k] for k in ["id", "status", "elapsed", "metrics", "error"]}, indent=2))
        if result["status"] == "failed":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
