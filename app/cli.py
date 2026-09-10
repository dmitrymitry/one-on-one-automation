import argparse

from .config import get_settings
from .service import build_automation


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Vegas 1:1 automation jobs once")
    parser.add_argument(
        "--job",
        choices=("cycle", "transcripts", "followups", "send-summary"),
        default="cycle",
    )
    parser.add_argument(
        "--meeting-id",
        help="Meeting to dispatch a reviewed summary for (required by --job send-summary)",
    )
    args = parser.parse_args()
    if args.job == "send-summary" and not args.meeting_id:
        parser.error("--job send-summary requires --meeting-id")

    automation = build_automation(get_settings())
    if args.job == "send-summary":
        print(automation.send_meeting_summary(args.meeting_id))
        return
    result = {
        "cycle": automation.run_cycle,
        "transcripts": automation.process_transcripts,
        "followups": automation.send_followups,
    }[args.job]()
    print(result)


if __name__ == "__main__":
    main()
