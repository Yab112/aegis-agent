"""
Helper script to scaffold your knowledge base folder structure.
Run once after cloning to create the right directories with placeholder files.

Usage: python scripts/seed_docs.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

KB = Path(__file__).parent.parent / "docs" / "knowledge_base"

PROJECTS = {
    "car_rental_app": "Full-stack car rental platform with Supabase, Next.js, and Chapa payments.",
    "cli_tool": "A command-line developer tool — describe yours here.",
    "general": "Your profile, skills, and general background.",
}

TEMPLATE = """# {title}

## Overview

{description}

## Tech stack

- List your technologies here

## Key decisions

- Why did you choose this approach?

## Outcome

- What was the result?
"""

for project, description in PROJECTS.items():
    project_dir = KB / project
    project_dir.mkdir(parents=True, exist_ok=True)

    overview_file = project_dir / "overview.md"
    if not overview_file.exists():
        overview_file.write_text(
            TEMPLATE.format(
                title=project.replace("_", " ").title(),
                description=description,
            )
        )
        print(f"Created: {overview_file.relative_to(KB.parent.parent)}")
    else:
        print(f"Exists (skipped): {overview_file.relative_to(KB.parent.parent)}")

print(f"\nKnowledge base ready at: {KB}")
print("Edit the overview.md files, add code files, then run: python scripts/ingest.py")
