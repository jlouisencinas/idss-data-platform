"""
scripts/test_delisted.py
------------------------
Local tests for the delisted (*) agent handling in transform_service.
Validates that "*" agents are now INCLUDED and their marker survives grouping.

Run:
  py scripts/test_delisted.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.transform_service import _parse_agent_row, build_dataframe, finalize_consolidated


def row(merged, *nums):
    """Build a raw row: col0 blank, col1='CODE NAME', then numeric cells."""
    return ["", merged] + [str(n) for n in (nums or [0] * 9)]


passed = total = 0
def check(label, cond):
    global passed, total
    total += 1
    if cond:
        passed += 1
        print(f"   ✓ {label}")
    else:
        print(f"   ✗ FAIL: {label}")


print("── _parse_agent_row ──")
# P1: delisted agent is INCLUDED (not None) and keeps the "*"
r1 = _parse_agent_row(row("1234567 JUAN CRUZ*"))
check("P1 delisted row included", r1 is not None)
check("P1 marker kept on name", r1 is not None and r1[1].endswith("*"))

# P2: normal agent unaffected
r2 = _parse_agent_row(row("7654321 MARIA SANTOS"))
check("P2 normal included", r2 is not None and not r2[1].endswith("*"))

# P4: manager header still skipped even with "*"
r4 = _parse_agent_row(row("BM: SOME MANAGER*"))
check("P4 manager header skipped", r4 is None)

# P7: '*' mid-name is NOT delisted
r7 = _parse_agent_row(row("3333333 JU*AN DELA CRUZ"))
check("P7 mid-name * not treated as delisted", r7 is not None and not r7[1].endswith("*"))

print("\n── build_dataframe ──")
# P1 end-to-end
df = build_dataframe([row("1234567 JUAN CRUZ*")], [], "May 31, 2026")
n = df.loc[df["AGENT CODE"] == "1234567", "AGENT NAME"].iloc[0]
check("P1 df keeps delisted marker", n.endswith("*"))

# P3 marker survives even when a LONGER non-* variant exists for same code
df3 = build_dataframe(
    [row("1111111 JUAN CRUZ*"), row("1111111 JUAN CRUZ JUNIOR")], [], "May 31, 2026"
)
sub = df3[df3["AGENT CODE"] == "1111111"]
check("P3 single grouped row", len(sub) == 1)
check("P3 marker preserved over longer name", sub["AGENT NAME"].iloc[0].endswith("*"))

# P5 space-before-star normalizes and re-marks
df5 = build_dataframe([row("2222222 MARIA *")], [], "May 31, 2026")
n5 = df5.loc[df5["AGENT CODE"] == "2222222", "AGENT NAME"].iloc[0]
check("P5 normalized + remarked", n5.endswith("*") and "  " not in n5)

# P2 normal stays clean in df
df2 = build_dataframe([row("7654321 MARIA SANTOS")], [], "May 31, 2026")
n2 = df2.loc[df2["AGENT CODE"] == "7654321", "AGENT NAME"].iloc[0]
check("P2 normal name has no marker", not n2.endswith("*"))

print("\n── finalize_consolidated ──")
# Marker must survive the cross-file grouping in finalize too
dfa = build_dataframe([row("1234567 JUAN CRUZ*"), row("7654321 MARIA SANTOS")], [], "May 31, 2026")
fin = finalize_consolidated(dfa.copy())
fn = fin.loc[fin["AGENT CODE"] == "1234567", "AGENT NAME"].iloc[0]
check("finalize keeps delisted marker", fn.endswith("*"))
check("finalize keeps normal clean",
      not fin.loc[fin["AGENT CODE"] == "7654321", "AGENT NAME"].iloc[0].endswith("*"))

print("\n" + "=" * 40)
print(f"  {passed}/{total} checks passed", "✅" if passed == total else "✗ SOME FAILED")
print("=" * 40)
sys.exit(0 if passed == total else 1)
