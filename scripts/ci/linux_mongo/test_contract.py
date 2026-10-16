import tempfile
import unittest
from pathlib import Path
from verify_results import verify

class GateTests(unittest.TestCase):
    def check(self, body, minimum=1):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"result.xml"
            p.write_text(body)
            return verify(p, minimum)
    def test_pass(self):
        self.assertEqual(self.check('<testsuites><testsuite><testcase name="ok"/></testsuite></testsuites>')["executed"], 1)
    def test_reject_outcomes(self):
        for tag in ("skipped", "failure", "error"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.check(f'<testsuite><testcase><{tag}/></testcase></testsuite>')
    def test_empty_and_minimum(self):
        for body, minimum in (("<testsuite/>",1),('<testsuite><testcase/></testsuite>',2)):
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                self.check(body,minimum)
    def test_missing(self):
        with self.assertRaises(FileNotFoundError):
            verify("does-not-exist.xml",1)

if __name__ == "__main__":
    unittest.main()
