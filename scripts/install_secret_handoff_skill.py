"""Install the BROWT secret-handoff instructions in the current user's skill folder."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate without installing files.")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "secret-handoff"
    destination = Path.home() / ".agents" / "skills" / "secret-handoff"
    files = (Path("SKILL.md"), Path("agents/openai.yaml"))
    content = {}
    try:
        for relative in files:
            origin = source / relative
            target = destination / relative
            content[relative] = origin.read_bytes()
            if target.is_symlink():
                raise ValueError(f"Refusing to replace a symbolic link: {target}")
            if target.exists() and target.read_bytes() != content[relative]:
                raise ValueError(f"A different personal skill already exists: {target}")
        if args.check:
            print(f"Skill validated. Personal installation target: {destination}")
            print("No files installed.")
            return 0
        for relative in files:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                continue
            with target.open("xb") as stream:
                stream.write(content[relative])
        print(f"Personal skill installed: {destination}")
        print("This procedure contains no API keys or tokens.")
        return 0
    except (OSError, ValueError) as error:
        print(f"Installation stopped: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
