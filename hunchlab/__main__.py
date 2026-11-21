"""Verify a study design, propose a revision, score responses, or replay a verdict."""

import argparse
import csv
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from .study import propose_paths, replay_paths, verify_paths


def save_new_json(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent) as temporary:
        temporary.write(content.encode("utf-8"))
        temporary.flush()
        # Publish the complete file without replacing a previous decision or proposal.
        os.link(temporary.name, path)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "analyze":
        from .analyze import main as analyze
        return analyze(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("analyze", help="Score the fielded Flashpoint survey and run paired statistics")
    verify = commands.add_parser("verify", help="Check the design and optionally score responses")
    propose = commands.add_parser("propose", help="Assemble a local candidate for a blocked design")
    replay = commands.add_parser("replay", help="Reproduce a saved verdict from its exact inputs")
    replay.add_argument("record", type=Path)
    for command in (verify, propose, replay):
        command.add_argument("claim", type=Path)
        command.add_argument("instrument", type=Path)
    for command in (verify, replay):
        command.add_argument("--responses", type=Path)
    for command in (verify, propose):
        command.add_argument("--save", type=Path, help="Save JSON to a new file")
    propose.add_argument("--revision", required=True, help="New local revision identifier")
    args = parser.parse_args(argv)

    try:
        if args.command is None:
            parser.print_help()
            return 0
        if args.command == "verify":
            result = verify_paths(args.claim, args.instrument, args.responses)
        elif args.command == "propose":
            result = propose_paths(args.claim, args.instrument, args.revision)
        else:
            result = replay_paths(args.record, args.claim, args.instrument, args.responses)
        content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if getattr(args, "save", None) is not None:
            save_new_json(args.save, content)
        sys.stdout.write(content)
    except (KeyError, OSError, TypeError, ValueError, csv.Error) as error:
        print(f"Signal to Study: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
