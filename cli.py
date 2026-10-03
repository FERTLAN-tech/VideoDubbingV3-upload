"""Command-line version: python cli.py <YouTube URL> [Language]"""
import logging
import sys

from dubbing import config
from dubbing.costs import CostTracker, UsageLedger
from dubbing.languages import DEFAULT_LANGUAGE, LANGUAGES
from dubbing.logging_setup import setup_logging
from dubbing.pipeline import friendly_error, run_pipeline
from dubbing.settings import apply_settings, load_settings, usage_path


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python cli.py <YouTube URL> [{'|'.join(LANGUAGES)}]")
        return 2
    url = sys.argv[1]
    language = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_LANGUAGE
    apply_settings(load_settings())          # same keys / models / timing as the app
    tracker = CostTracker(UsageLedger(usage_path()), pricing=config.PRICING, budget=config.MAX_SPEND_PER_VIDEO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(message)s"))
    print("Log file:", setup_logging(console))
    try:
        result = run_pipeline(url, language, on_progress=lambda p, m: print(f"[{p:5.1f}%] {m}"),
                              tracker=tracker)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("dubbing").exception("Job failed")
        print("\nERROR:", friendly_error(exc))
        return 1
    finally:
        cost = tracker.job_summary()
        print("Estimated spend: " + ", ".join(
            f"{role} ${cost[role]['cost']:.4f}" + (" (+ price unknown)" if cost[role]["unknown"] else "")
            for role in ("analysis", "script", "voice", "total")))
        print(f"API calls: {cost['api_calls']}, reused from cache: {cost['cache_hits']} "
              f"(saved about ${cost['saved_usd']:.4f})")
    print("\nFINAL VIDEO READY:", result.video_path)
    for w in result.warnings:
        print("WARNING:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
