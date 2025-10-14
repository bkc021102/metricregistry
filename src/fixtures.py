"""A student cohort with the ambiguous cases that make the three numbers differ,
and the correct answer under each definition written down in advance.
"""

from __future__ import annotations

import random
import sqlite3

from .registry import MetricDefinition, Registry

CENSUS_DATE = "2026-10-15"
TERM_START = "2026-09-28"

SCHEMA = """
CREATE TABLE student (
  id            INTEGER PRIMARY KEY,
  applied_at    TEXT NOT NULL,
  enrolled_at   TEXT,
  withdrawn_at  TEXT,
  deferred_to   TEXT,
  programme     TEXT NOT NULL,
  fee_status    TEXT NOT NULL,   -- home | international
  is_dual       INTEGER NOT NULL DEFAULT 0
);
"""


def build(seed: int = 20260911) -> tuple[sqlite3.Connection, dict[str, int]]:
    """Return (connection, expected counts under each definition)."""
    rnd = random.Random(seed)
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)

    rows: list[tuple] = []
    next_id = 1

    def add(enrolled, withdrawn, deferred, dual=0, n=1):
        nonlocal next_id
        for _ in range(n):
            rows.append((
                next_id, "2026-03-01", enrolled, withdrawn, deferred,
                rnd.choice(["BSc CS", "BA Hist", "MSc Data", "LLB"]),
                rnd.choice(["home", "international"]), dual,
            ))
            next_id += 1

    # --- the unambiguous majority -----------------------------------------
    add("2026-09-20", None, None, n=13_200)

    # --- the ambiguous cases, each counted differently by each team --------
    #: enrolled before term, withdrew BEFORE census
    add("2026-09-20", "2026-10-01", None, n=310)
    #: enrolled before term, withdrew AFTER census
    add("2026-09-20", "2026-11-02", None, n=190)
    #: late enrolment, after term start but before census
    add("2026-10-05", None, None, n=420)
    #: enrolled, then deferred to next year
    add("2026-09-20", None, "2027-09-27", n=260)
    #: deferred without ever enrolling
    add(None, None, "2027-09-27", n=140)
    #: dual enrolment - one person, two programmes
    add("2026-09-20", None, None, dual=1, n=180)
    #: applied but never enrolled
    add(None, None, None, n=2_400)

    conn.executemany(
        "INSERT INTO student VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()

    # GROUND TRUTH, derived from the counts above rather than from the SQL.
    core = 13_200 + 180          # enrolled, never withdrew, not deferred
    expected = {
        # Registrar: enrolled by TERM START, still active at census.
        "registrar": core + 190,
        # Finance: anyone enrolled at any point before census who had not
        # withdrawn by census - including late enrolments.
        "finance": core + 190 + 420,
        # Marketing: everyone who ever enrolled, plus deferrals, because the
        # campaign is measured on conversions.
        "marketing": core + 190 + 310 + 420 + 260,
    }
    return conn, expected


REGISTRY = Registry()

REGISTRY.register(MetricDefinition(
    name="enrolled_students_registrar",
    version=1,
    subject="enrolled_students",
    grain="one row per student record",
    owner="registry-office",
    description=(
        "Students enrolled on or before term start who had not withdrawn or "
        "deferred as at the census date."
    ),
    sql=f"""
        SELECT COUNT(*) FROM student
         WHERE enrolled_at IS NOT NULL
           AND enrolled_at <= '{TERM_START}'
           AND deferred_to IS NULL
           AND (withdrawn_at IS NULL OR withdrawn_at > '{CENSUS_DATE}')
    """,
))

REGISTRY.register(MetricDefinition(
    name="enrolled_students_finance",
    version=1,
    subject="enrolled_students",
    grain="one row per student record",
    owner="finance",
    description=(
        "Fee-liable students: enrolled at any point up to the census date and "
        "not withdrawn by it. Late enrolments are included because they are "
        "invoiced."
    ),
    sql=f"""
        SELECT COUNT(*) FROM student
         WHERE enrolled_at IS NOT NULL
           AND enrolled_at <= '{CENSUS_DATE}'
           AND deferred_to IS NULL
           AND (withdrawn_at IS NULL OR withdrawn_at > '{CENSUS_DATE}')
    """,
))

REGISTRY.register(MetricDefinition(
    name="enrolled_students_marketing",
    version=1,
    subject="enrolled_students",
    grain="one row per student record",
    owner="marketing",
    description=(
        "Conversions: anyone who ever enrolled, including those who later "
        "withdrew or deferred, because the campaign delivered them."
    ),
    sql=f"""
        SELECT COUNT(*) FROM student
         WHERE enrolled_at IS NOT NULL
           AND enrolled_at <= '{CENSUS_DATE}'
    """,
))

#: A second subject where everyone genuinely agrees, so the monitor has a
#: negative case. A monitor that fires on everything is not a monitor.
REGISTRY.register(MetricDefinition(
    name="applications_received",
    version=1,
    subject="applications",
    grain="one row per student record",
    owner="admissions",
    description=(
        "Every application record received for the cycle, regardless of "
        "whether it converted to an enrolment."
    ),
    sql="SELECT COUNT(*) FROM student",
))
REGISTRY.register(MetricDefinition(
    name="applications_total",
    version=1,
    subject="applications",
    grain="one row per student record",
    owner="planning",
    description=(
        "Every application record received for the cycle, counted "
        "independently by planning as a control on admissions."
    ),
    sql="SELECT COUNT(id) FROM student",
))
