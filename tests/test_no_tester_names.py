"""SC-54: a tester's name must never be committed to this public repo.

The names are known from the invite material in private/ (git-ignored: files named <name>-invite.md or
<name>-alerts-qr.png). This test reads them from there at run time, so no name is ever written in the repo, and
fails if any appears in a tracked file. It runs on a machine that has private/ and is skipped everywhere else
(CI has no invite material), so it protects the place where the documents are actually written."""
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE = os.path.join(ROOT, "private")


def tester_names():
    names = set()
    if os.path.isdir(PRIVATE):
        for f in os.listdir(PRIVATE):
            m = re.match(r"([a-z]+)-(invite|alerts)", f.lower())
            if m and len(m.group(1)) >= 4:
                names.add(m.group(1))
    return sorted(names)


@unittest.skipUnless(tester_names(), "no invite material in private/ on this machine")
class NoTesterNames(unittest.TestCase):
    def test_no_tracked_file_mentions_a_tester_by_name(self):
        names = tester_names()
        pattern = re.compile(r"(?<![A-Za-z])(" + "|".join(map(re.escape, names)) + r")(?![A-Za-z])", re.IGNORECASE)
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split("\n")
        found = []
        for f in files:
            if not f or f.endswith((".png", ".woff2", ".webp", ".ico", ".svg")):
                continue
            try:
                text = open(os.path.join(ROOT, f), encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            if pattern.search(text):
                found.append(f)
        # file names only, never the matching text or the name: this message may end up in a log
        self.assertEqual(found, [], "a tester's name appears in these tracked files; refer to them as 'a tester'")


if __name__ == "__main__":
    unittest.main()
