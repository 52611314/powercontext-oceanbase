import copy
import unittest
from orders import list_orders

DATA = [
    {"id": 3, "customer": "alice", "status": "pending"},
    {"id": 1, "customer": "alice", "status": "paid"},
    {"id": 2, "customer": "bob", "status": "paid"},
    {"id": 4, "customer": "bob", "status": "cancelled"},
]

class Regression(unittest.TestCase):
    def test_default_sorted(self):
        self.assertEqual([o["id"] for o in list_orders(DATA)], [1, 2, 3, 4])
    def test_customer(self):
        self.assertEqual([o["id"] for o in list_orders(DATA, customer="alice")], [1, 3])
        self.assertEqual(list_orders(DATA, customer="missing"), [])
    def test_no_mutation(self):
        before = copy.deepcopy(DATA)
        result = list_orders(DATA)
        result[0]["status"] = "changed"
        self.assertEqual(DATA, before)
    def test_empty(self):
        self.assertEqual(list_orders([]), [])

class Target(unittest.TestCase):
    def test_status(self):
        for status, ids in [("pending", [3]), ("paid", [1, 2]), ("cancelled", [4])]:
            with self.subTest(status=status):
                self.assertEqual([o["id"] for o in list_orders(DATA, status=status)], ids)
    def test_combined(self):
        self.assertEqual([o["id"] for o in list_orders(DATA, customer="alice", status="paid")], [1])
        self.assertEqual(list_orders(DATA, customer="alice", status="cancelled"), [])
    def test_invalid(self):
        for rows in [DATA, []]:
            for status in ["", "PAID", "unknown"]:
                with self.subTest(rows=bool(rows), status=status):
                    with self.assertRaises(ValueError):
                        list_orders(rows, status=status)

if __name__ == "__main__":
    unittest.main()