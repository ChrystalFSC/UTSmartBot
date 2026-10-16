"""Tests for the structured UTS data and the chatbot's tools (no API key needed).

Run with:  python -m unittest discover tests
"""
import unittest

from campus.tools import (
    PROGRAMMES, STAFF, estimate_fees, find_programmes, lookup_staff, resolve_programme, run_tool,
)


def programme(code):
    return next(p for p in PROGRAMMES if p["code"] == code)


class ExtractedDataTest(unittest.TestCase):
    """Spot checks against the UTS website fee schedule (knowledge/uts_website/tuitionfees.html)
    and the older knowledge base."""

    def test_bcs_fees(self):
        bcs = programme("BCS")
        self.assertTrue(bcs["ftes"])
        self.assertEqual(bcs["fees"]["actual"], {"total": 52440, "per_year": 14983})
        self.assertEqual(bcs["fees"]["sarawakian"], {"total": 0, "per_year": 0, "scheme": "FTES"})
        self.assertEqual(bcs["fees"]["malaysian"]["total"], 36708)
        self.assertEqual(bcs["fees"]["malaysian"]["scheme"], "30% UTS Bursary Scheme")
        self.assertEqual(bcs["fees"]["international"]["per_year"], 19743)

    def test_non_ftes_programme(self):
        bba = programme("BBA")
        self.assertFalse(bba["ftes"])
        self.assertEqual(bba["fees"]["sarawakian"]["total"], 24325)
        self.assertEqual(bba["fees"]["sarawakian"]["scheme"], "50% UTS Bursary Scheme")
        self.assertEqual(bba["fees"]["malaysian"]["scheme"], "25% UTS Bursary Scheme")

    def test_new_programme_from_website(self):
        marine = next(p for p in PROGRAMMES if "Marine" in p["name"])
        self.assertEqual((marine["credits"], marine["duration_years"], marine["ftes"]), (140, 4, True))
        self.assertEqual(marine["school"], "School of Engineering and Technology")

    def test_programme_details(self):
        bcs = programme("BCS")
        self.assertEqual((bcs["credits"], bcs["duration_years"]), (126, 3.5))
        self.assertEqual(bcs["school"], "School of Computing & Creative Media")
        self.assertIn("STPM", bcs["entry_requirements"])

    def test_postgraduate_fees_are_not_mixed_with_bachelor(self):
        msc = next(p for p in PROGRAMMES if p["name"] == "Master of Science in Architecture")
        self.assertEqual(msc["level"], "master")
        self.assertNotEqual(msc["code"], "BSA")
        self.assertNotIn("international", msc["fees"])

    def test_all_programmes_have_fees(self):
        self.assertEqual(len(PROGRAMMES), 36)
        self.assertEqual(sum(p["ftes"] for p in PROGRAMMES), 16)
        for p in PROGRAMMES:
            self.assertTrue(p["fees"].get("local") or p["fees"].get("actual"), p["name"])

    def test_management_roles_are_current(self):
        vc = next(s for s in STAFF if "Shahril" in s["name"])
        self.assertIn({"position": "Vice Chancellor", "department": "Management Team",
                       "source": "UTS website, management team (current)"}, vc["roles"])
        self.assertFalse(any(s["name"] == "Vice Chancellor" for s in STAFF))  # a heading is not a person

    def test_staff_records_are_merged(self):
        alan = [s for s in STAFF if s["email"] == "alan.ting@uts.edu.my"]
        self.assertEqual(len(alan), 1)
        self.assertGreaterEqual(len(alan[0]["roles"]), 2)
        self.assertFalse(any(s["name"].startswith(("•", "?")) for s in STAFF))

    def test_emails_are_complete(self):
        for s in STAFF:
            if s["email"]:
                self.assertRegex(s["email"], r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")


class ResolveProgrammeTest(unittest.TestCase):
    def test_codes_names_and_levels(self):
        cases = {
            "BCS": "Bachelor of Computer Science (Hons)",
            "computer science": "Bachelor of Computer Science (Hons)",
            "phd computing": "Doctor of Philosophy (PhD) in Computing",
            "MBA": "Master of Business Administration",
            "interior design": "Bachelor of Arts (Honours) Interior Design",
            "marine": "Bachelor of Marine Engineering Technology (Honours)",
            "foundation science": "Foundation in Science",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(resolve_programme(query)[0]["name"], expected)

    def test_unknown_programme(self):
        self.assertEqual(resolve_programme("zzzz qqqq"), [])


class EstimateFeesTest(unittest.TestCase):
    def test_malaysian_bachelor_total_includes_registration(self):
        result = estimate_fees("BCS", "malaysian")
        self.assertEqual(result["tuition_total"], 36708)
        self.assertEqual(result["registration_fees"]["semesters"], 7)
        self.assertEqual(result["registration_fees"]["total"], 350 + 150 * 7)
        self.assertEqual(result["estimated_total"], 36708 + 1400)
        self.assertEqual(result["full_fee_before_bursary"], 52440)

    def test_sarawakian_ftes_programme_is_free(self):
        result = estimate_fees("BCS", "sarawakian")
        self.assertEqual(result["tuition_total"], 0)
        self.assertEqual(result["estimated_total"], 1400)  # registration fees are listed separately
        self.assertIn("FTES programme", result["ftes"])

    def test_sarawakian_non_ftes_programme_has_bursary(self):
        result = estimate_fees("BBA", "sarawakian")
        self.assertEqual(result["tuition_total"], 24325)
        self.assertNotIn("ftes", result)

    def test_old_student_types_still_work(self):
        self.assertEqual(estimate_fees("BCS", "local")["student_type"], "malaysian")
        self.assertEqual(estimate_fees("BCS", "sarawakian_b40")["student_type"], "sarawakian")

    def test_international_total_is_per_year_times_duration(self):
        result = estimate_fees("mobile game development", "international")
        self.assertEqual(result["tuition_total"], round(24210 * 3.5))
        self.assertTrue(any("per year" in n for n in result["notes"]))

    def test_foundation_fee_and_sponsorship(self):
        result = estimate_fees("foundation in arts", "sarawakian")
        self.assertEqual(result["tuition_total"], 10000)
        self.assertTrue(any("Full sponsorship" in n for n in result["notes"]))

    def test_postgraduate_modes_and_scholarship(self):
        self.assertEqual(estimate_fees("master of engineering", "malaysian", "part_time")["tuition_total"], 14750)
        self.assertEqual(estimate_fees("master of engineering", "sarawakian")["tuition_total"], 8550)
        self.assertEqual(estimate_fees("MBA", "malaysian")["tuition_total"], 21850)
        self.assertNotIn("registration_fees", estimate_fees("MBA", "malaysian"))

    def test_missing_fee_is_reported_not_invented(self):
        result = estimate_fees("Master of Architecture", "malaysian", "part_time")
        self.assertIsNone(result["tuition_total"])
        self.assertTrue(any("No part-time fee" in n for n in result["notes"]))

    def test_unknown_programme(self):
        self.assertIn("error", estimate_fees("zzzz qqqq"))


class FindProgrammesTest(unittest.TestCase):
    def test_interest(self):
        names = [p["name"] for p in find_programmes(interest="computers", level="undergraduate")["programmes"]]
        self.assertIn("Bachelor of Computer Science (Hons)", names)
        self.assertIn("Bachelor of Mobile Game Development (Honours)", names)

    def test_budget_filter(self):
        result = find_programmes(level="undergraduate", student_type="malaysian", max_fee_per_year=11000)
        names = [p["name"] for p in result["programmes"]]
        self.assertIn("Bachelor of Computer Science (Hons)", names)
        self.assertTrue(all(p["approx_fee_per_year_malaysian"] <= 11000 for p in result["programmes"]))

    def test_all_student_types_when_unknown(self):
        fees = find_programmes(name="BCS")["programmes"][0]["fees"]
        self.assertEqual(set(fees), {"sarawakian", "malaysian", "international"})

    def test_catalogue_lists_everything(self):
        result = find_programmes()
        self.assertEqual(result["count"], 36)
        self.assertEqual(result["programmes"][0]["level"], "foundation")

    def test_qualification_excerpt(self):
        bcs = find_programmes(name="BCS", qualification="UEC")["programmes"][0]
        self.assertIn("UEC", bcs["entry_requirements"])
        self.assertNotIn("STAM", bcs["entry_requirements"])


class LookupStaffTest(unittest.TestCase):
    def first(self, query):
        return lookup_staff(query)["matches"][0]

    def test_by_name(self):
        self.assertEqual(self.first("Alan Ting")["email"], "alan.ting@uts.edu.my")

    def test_by_role(self):
        self.assertIn("RABUAN", self.first("who is the bursar")["name"].upper())  # current, from the website
        self.assertIn("Shahril", self.first("who is the vice chancellor")["name"])
        self.assertIn("MUNSHID", self.first("deputy vice chancellor")["name"].upper())
        self.assertIn("Jimmy", self.first("registrar")["name"])
        self.assertEqual(self.first("IT manager")["email"], "shukri@uts.edu.my")
        self.assertIn("Hazman", self.first("hostel warden")["name"])

    def test_current_school_pages_win(self):
        # The knowledge base still lists Ts. Dr. Chew Kim Mey as BCS Head of Programme; scm.uts.edu.my does not.
        self.assertIn("Khairunnisa", self.first("who is the hop of bcs")["name"])
        self.assertIn("Elton Wong", self.first("Elton Wong")["name"])
        chew = self.first("Chew Kim Mey")
        self.assertFalse(chew["on_current_website"])

    def test_partial_name_matches_are_flagged(self):
        self.assertIn("name_match", lookup_staff("John Smith"))
        self.assertNotIn("name_match", lookup_staff("Elton Wong"))

    def test_extension_becomes_full_number(self):
        self.assertEqual(self.first("IT manager")["phone"], "084-367348")

    def test_no_match(self):
        self.assertEqual(lookup_staff("zzzz qqqq")["matches"], [])


class RunToolTest(unittest.TestCase):
    def test_bad_arguments_do_not_raise(self):
        self.assertIn("error", run_tool("estimate_fees", {"programm": "BCS"}))
        self.assertIn("error", run_tool("no_such_tool", {}))


if __name__ == "__main__":
    unittest.main()
