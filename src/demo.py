"""A walkthrough: three numbers, and exactly why they differ.

Run: python -m src.demo
"""

from __future__ import annotations

from .fixtures import CENSUS_DATE, REGISTRY, TERM_START, build
from .registry import (
    MetricDefinition, Registry, breaches, check_divergence, definition_changes,
    evaluate, lineage_of,
)


def main() -> None:
    conn, expected = build()

    print("\n  METRICREGISTRY - forty minutes arguing about the number")
    print("  " + "=" * 74)
    print(f"  Census date {CENSUS_DATE}, term start {TERM_START}.\n")

    print("  THE NUMBERS IN THE ROOM")
    print("  " + "-" * 74)
    for d in REGISTRY.by_subject("enrolled_students"):
        value = evaluate(conn, d).value
        print(f"    {d.owner:<16} {value:>9,.0f}   {d.description.split('.')[0]}.")

    print("\n  All three are correct. They are measuring different things.\n")

    print("  WHAT EACH DEFINITION DOES WITH THE AMBIGUOUS CASES")
    print("  " + "-" * 74)
    cases = [
        ("withdrew BEFORE census", 310),
        ("withdrew AFTER census", 190),
        ("enrolled late, before census", 420),
        ("enrolled then deferred", 260),
        ("deferred, never enrolled", 140),
    ]
    print(f"    {'case':<32}{'n':>7}   registrar  finance  marketing")
    for label, n in cases:
        inc = {
            "withdrew BEFORE census": ("no", "no", "YES"),
            "withdrew AFTER census": ("YES", "YES", "YES"),
            "enrolled late, before census": ("no", "YES", "YES"),
            "enrolled then deferred": ("no", "no", "YES"),
            "deferred, never enrolled": ("no", "no", "no"),
        }[label]
        print(f"    {label:<32}{n:>7}   {inc[0]:<10}{inc[1]:<9}{inc[2]}")

    print("\n  THE MONITOR")
    print("  " + "-" * 74)
    fired = breaches(conn, REGISTRY)
    for d in check_divergence(conn, REGISTRY):
        flag = "ALERT" if d.breached else "ok"
        print(f"    [{flag:<5}] {d.describe()}")

    print(f"\n    {len(fired)} alert(s). Note that `applications` does NOT fire:")
    print("    a monitor that flags everything gets muted, and then it is not")
    print("    a monitor.\n")

    print("  LINEAGE: the inputs ARE the disagreement")
    print("  " + "-" * 74)
    for name in ("enrolled_students_registrar", "enrolled_students_marketing"):
        d = REGISTRY.current(name)
        print(f"    {d.owner:<16} reads {', '.join(sorted(lineage_of(d)))}")

    print("\n  WHEN A NUMBER MOVES BECAUSE THE DEFINITION MOVED")
    print("  " + "-" * 74)
    r = Registry()
    r.register(MetricDefinition(
        "enrolled_students_finance", 1, "enrolled_students", "per student",
        "finance", "Original: everyone enrolled by the census date." + " " * 10,
        f"SELECT COUNT(*) FROM student WHERE enrolled_at <= '{CENSUS_DATE}'"))
    r.register(MetricDefinition(
        "enrolled_students_finance", 2, "enrolled_students", "per student",
        "finance",
        "Amended after audit: exclude students who deferred." + " " * 10,
        f"SELECT COUNT(*) FROM student WHERE enrolled_at <= '{CENSUS_DATE}' "
        f"AND deferred_to IS NULL"))

    for c in definition_changes(conn, r):
        print(f"    {c.name} v{c.from_version} -> v{c.to_version}: "
              f"{c.old_value:,.0f} -> {c.new_value:,.0f} "
              f"({c.moved_by:+,.0f})")
    print("    That movement is not a business event. Reporting it as one is")
    print("    how people stop trusting the dashboard.\n")


if __name__ == "__main__":
    main()
