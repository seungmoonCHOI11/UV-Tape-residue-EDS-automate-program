import unittest

from app.analysis import normalize_conditions, condition_point_sequence
from app.reports import engineering_summary, reference_summary


class V23752ScopeTests(unittest.TestCase):
    def test_substrate_mapping_is_by_wafer_not_measurement_point(self):
        conditions = normalize_conditions([
            {"power": "150", "time": "120", "wafers": "1,4,5,9", "points": "1-3"}
        ])
        seq = condition_point_sequence(
            conditions,
            {"1": "SiCN", "4": "SiCN", "5": "SiCN", "9": "Si"},
            "SiCN",
        )
        self.assertEqual(len(seq), 12)
        self.assertTrue(all(x["substrate_type"] == "Si" for x in seq if x["wafer"] == 9))
        self.assertTrue(all(x["substrate_type"] == "SiCN" for x in seq if x["wafer"] in (1, 4, 5)))
        # P3 on W9 must still be Si: substrate follows W9, not P3.
        self.assertEqual(next(x for x in seq if x["wafer"] == 9 and x["point"] == 3)["substrate_type"], "Si")

    def test_w9_is_excluded_from_main_and_grouped_as_reference(self):
        records = []
        for wafer in (1, 4, 5):
            for point in (1, 2, 3):
                records.append({
                    "power": "250W", "time": "120s", "wafer": wafer, "point": point,
                    "substrate_type": "SiCN", "human_result": None,
                    "features": {"c_roi_global_ratio": 3.0, "o_roi_global_ratio": 4.0, "result": "Residue"},
                })
        for point in (1, 2, 3):
            records.append({
                "power": "250W", "time": "120s", "wafer": 9, "point": point,
                "substrate_type": "Si", "human_result": None,
                "features": {"c_roi_global_ratio": 1.0, "o_roi_global_ratio": 1.0, "result": "Non-residue"},
            })

        main = engineering_summary(records)
        refs = reference_summary(records, 9)
        self.assertEqual(main[0]["points"], 9)
        self.assertEqual(main[0]["excluded_other_wafers"], 3)
        self.assertEqual(main[0]["residue"], 9)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["substrate"], "Si")
        self.assertEqual(refs[0]["points"], 3)
        self.assertEqual(refs[0]["residue"], 0)


if __name__ == "__main__":
    unittest.main()
