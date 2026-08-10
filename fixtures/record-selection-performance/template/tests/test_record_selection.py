import unittest

from src.record_selection import select_records


class SelectRecordsTests(unittest.TestCase):
    def test_preserves_request_order_and_ignores_missing_ids(self):
        records = [
            {"id": "b", "value": 2},
            {"id": "a", "value": 1},
            {"id": "c", "value": 3},
        ]

        self.assertEqual(
            select_records(records, ["a", "missing", "c"]),
            [records[1], records[2]],
        )

    def test_repeated_requests_produce_repeated_objects(self):
        record = {"id": "a", "value": 1}

        result = select_records([record], ["a", "a"])

        self.assertEqual(result, [record, record])
        self.assertIs(result[0], record)
        self.assertIs(result[1], record)


if __name__ == "__main__":
    unittest.main()
