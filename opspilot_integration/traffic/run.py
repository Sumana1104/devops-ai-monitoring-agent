"""Opt-in wrapper; the three original repair gates stay OFF."""
import argparse
import os
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--enable-scaling", action="store_true")
    args = p.parse_args()
    os.environ["OPSPILOT_TRAFFIC_MONITORING"] = "1"
    os.environ["OPSPILOT_ENABLE_TRAFFIC_SCALING"] = "1" if args.enable_scaling else "0"
    sys.argv = [sys.argv[0]]
    from opspilot_integration.run_ai_bridge import main as start
    start(traffic=True, traffic_scaling=args.enable_scaling)


if __name__ == "__main__":
    main()
