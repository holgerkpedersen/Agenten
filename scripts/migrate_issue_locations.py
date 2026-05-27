import json
import os
import re
import sys

project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_dir)

from agent_files import locate_code


LINE_LOC_PAT = re.compile(r'([\w./\\-]+\.\w+):(\d+)(?:\s*-\s*\d+)?$')


def migrate_location(location, base_dir):
    if not location:
        return location, None

    parts = re.split(r'\s*[,;]\s*', location)
    resolved = []
    code_contexts = []

    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = LINE_LOC_PAT.match(part)
        if not m:
            resolved.append(part)
            continue

        fname = m.group(1)
        line_no = int(m.group(2))

        found = False
        for candidate in [fname, os.path.join(base_dir, fname)]:
            if os.path.exists(candidate):
                result = locate_code(filepath=candidate, line_no=line_no)
                if result.get("success") and result.get("name"):
                    resolved.append(f"{fname}:{result['name']}")
                    body = result.get("body", "")
                    sig = body.split("\n")[0][:120] if body else ""
                    ctx_part = f"{fname}:{result['name']}"
                    if sig:
                        ctx_part += f"\n  {sig}"
                    code_contexts.append(ctx_part)
                    found = True
                    break
                elif result.get("success"):
                    resolved.append(f"{fname}:{line_no}")
                    found = True
                    break

        if not found:
            resolved.append(part)

    new_location = ", ".join(resolved)
    new_context = "\n".join(code_contexts) if code_contexts else None
    return new_location, new_context


def main(dry_run=False):
    issues_path = os.path.join(project_dir, "docs", "issues", "observed", "issues.json")
    with open(issues_path, encoding="utf-8") as f:
        data = json.load(f)

    migrated = 0
    unchanged = 0
    errors = 0

    for issue in data.get("issues", []):
        old_loc = issue.get("location", "")
        if not old_loc:
            unchanged += 1
            continue

        new_loc, code_context = migrate_location(old_loc, project_dir)
        if new_loc == old_loc:
            unchanged += 1
        else:
            migrated += 1
            print(f"  {issue['id']}: {old_loc} -> {new_loc}")
            if not dry_run:
                issue["location"] = new_loc
                if code_context:
                    issue["code_context"] = code_context

    print(f"\nSummary: {migrated} migrated, {unchanged} unchanged, {errors} errors")

    if not dry_run and migrated > 0:
        data["meta"]["total"] = len(data["issues"])
        backup_path = issues_path + ".bak"
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Backup saved to: {backup_path}")
        with open(issues_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Updated: {issues_path}")
        print()
        print("To revert: copy the .bak file back")
    elif dry_run:
        print("DRY RUN — no changes written.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
