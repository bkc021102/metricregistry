"""Each definition must return its documented value on the ambiguous cases,
and the monitor must fire on disagreement without firing on agreement."""

from __future__ import annotations

import unittest

from src.fixtures import CENSUS_DATE, REGISTRY, build
from src.registry import (
    MetricDefinition, Registry, breaches, check_divergence, definition_changes,
    evaluate, lineage_of,
)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn, cls.expected = build()


class TestDefinitionsAreExecutable(Base):
    def test_every_definition_returns_its_documented_value(self):
        cases = {
            "enrolled_students_registrar": self.expected["registrar"],
            "enrolled_students_finance": self.expected["finance"],
            "enrolled_students_marketing": self.expected["marketing"],
        }
        for name, want in cases.items():
            with self.subTest(metric=name):
                got = evaluate(self.conn, REGISTRY.current(name)).value
                self.assertEqual(
                    int(got), want,
                    f"{name} returned {got:,.0f}, the documented answer on the "
                    f"ambiguous cases is {want:,}",
                )

    def test_the_three_numbers_genuinely_differ(self):
        values = {
            d.name: evaluate(self.conn, d).value
            for d in REGISTRY.by_subject("enrolled_students")
        }
        self.assertEqual(len(set(values.values())), 3,
                         f"all three should differ: {values}")

    def test_every_definition_carries_grain_owner_and_description(self):
        for name in REGISTRY.names():
            d = REGISTRY.current(name)
            self.assertTrue(d.grain)
            self.assertTrue(d.owner)
            self.assertGreater(len(d.description), 30,
                               f"{name} has no usable description")

    def test_a_definition_is_sql_not_prose(self):
        for name in REGISTRY.names():
            self.assertIn("select", REGISTRY.current(name).sql.lower())


class TestDivergenceMonitor(Base):
    def test_the_monitor_fires_on_the_enrolment_disagreement(self):
        fired = breaches(self.conn, REGISTRY)
        subjects = {d.subject for d in fired}
        self.assertIn("enrolled_students", subjects)

    def test_it_does_not_fire_where_everyone_agrees(self):
        # A monitor that fires on everything gets muted, at which point it is
        # not a monitor.
        fired = breaches(self.conn, REGISTRY)
        self.assertNotIn("applications", {d.subject for d in fired})

    def test_every_pair_within_a_subject_is_compared(self):
        divergences = check_divergence(self.conn, REGISTRY)
        enrolment = [d for d in divergences if d.subject == "enrolled_students"]
        self.assertEqual(len(enrolment), 3, "3 metrics means 3 pairs")

    def test_the_largest_disagreement_is_reported_first(self):
        divergences = check_divergence(self.conn, REGISTRY)
        relatives = [d.relative for d in divergences]
        self.assertEqual(relatives, sorted(relatives, reverse=True))

    def test_the_alert_names_both_definitions_and_the_gap(self):
        worst = check_divergence(self.conn, REGISTRY)[0]
        text = worst.describe()
        self.assertIn("registrar", text + worst.a.label + worst.b.label)
        self.assertIn("tolerance", text)
        self.assertIn("%", text)

    def test_tolerance_is_the_tighter_of_the_two_definitions(self):
        loose = MetricDefinition(
            "loose", 1, "s", "g", "o", "a" * 40, "SELECT 100", tolerance=0.5)
        tight = MetricDefinition(
            "tight", 1, "s", "g", "o", "a" * 40, "SELECT 110", tolerance=0.01)
        r = Registry()
        r.register(loose)
        r.register(tight)
        d = check_divergence(self.conn, r)[0]
        self.assertAlmostEqual(d.tolerance, 0.01)
        self.assertTrue(d.breached)


class TestVersioning(Base):
    def test_a_definition_cannot_be_edited_in_place(self):
        r = Registry()
        d = MetricDefinition("m", 1, "s", "g", "o", "d" * 40, "SELECT 1")
        r.register(d)
        with self.assertRaises(ValueError):
            r.register(MetricDefinition("m", 1, "s", "g", "o", "d" * 40, "SELECT 2"))

    def test_amending_publishes_a_new_version_and_keeps_the_old(self):
        r = Registry()
        r.register(MetricDefinition("m", 1, "s", "g", "o", "d" * 40,
                                    "SELECT COUNT(*) FROM student"))
        r.register(MetricDefinition(
            "m", 2, "s", "g", "o", "d" * 40,
            f"SELECT COUNT(*) FROM student WHERE enrolled_at <= '{CENSUS_DATE}'"))
        self.assertEqual(r.current("m").version, 2)
        self.assertEqual(r.at_version("m", 1).version, 1)

    def test_change_log_shows_when_a_number_moved_because_the_definition_moved(self):
        r = Registry()
        r.register(MetricDefinition("m", 1, "s", "g", "o", "d" * 40,
                                    "SELECT COUNT(*) FROM student"))
        r.register(MetricDefinition(
            "m", 2, "s", "g", "o", "d" * 40,
            "SELECT COUNT(*) FROM student WHERE enrolled_at IS NOT NULL"))

        changes = definition_changes(self.conn, r)
        self.assertEqual(len(changes), 1)
        self.assertNotEqual(changes[0].moved_by, 0.0)
        # A number that moved for this reason is not a business event, and
        # reporting it as one is how people lose trust in the dashboard.

    def test_no_change_log_entry_when_there_is_only_one_version(self):
        self.assertEqual(definition_changes(self.conn, REGISTRY), [])


class TestLineage(Base):
    def test_a_definition_declares_the_columns_it_reads(self):
        cols = lineage_of(REGISTRY.current("enrolled_students_registrar"))
        self.assertTrue(any("enrolled_at" in c for c in cols))
        self.assertTrue(any("withdrawn_at" in c for c in cols))

    def test_lineage_differs_between_definitions_of_the_same_subject(self):
        registrar = lineage_of(REGISTRY.current("enrolled_students_registrar"))
        marketing = lineage_of(REGISTRY.current("enrolled_students_marketing"))
        self.assertNotEqual(registrar, marketing,
                            "the difference in inputs IS the disagreement")


if __name__ == "__main__":
    unittest.main()
