# Bugs and fixes

## 2026-09-05 - one bad number in the warehouse file killed the whole upload

**What broke:** `_apply_warehouse_details` in the ingest module ran outside
the per-row try/except that the orders loop uses. A non-numeric
`storage_cost_annual` raised straight out of `load()`.

**What it meant for users:** a company uploads 1,300 perfectly good order
rows plus a warehouse file with one typo in it, and the entire import fails
with an exception instead of importing the orders and flagging the typo.

**Root cause:** the error-collecting pattern was applied to the loop that was
obviously risky (parsing user rows) and not to the second loop doing exactly
the same kind of parsing on a second user-supplied file.

**Fix:** wrapped the warehouse row body in the same try/except and appended
to the same errors list, so it reports "Rotterdam DC: storage_cost_annual is
not a number: notanumber" and carries on.

**The lesson:** when a function collects errors instead of raising them, every
path that touches user input has to go through that collector, not just the
main one. Grep for the other places parsing the same kind of input before
calling the pattern done.

## 2026-09-05 - validation errors leaked raw Python exception text

**What broke:** a non-numeric weight produced the error
"could not convert string to float: 'abc'" in the upload report.

**What it meant for users:** an error message that names no column and reads
like a crash, on a screen whose whole job is telling a non-technical user
which cell in their spreadsheet is wrong.

**Root cause:** `float()` was called directly and its ValueError was caught
by a handler that just stringified whatever it got.

**Fix:** `_number()` now takes the field name and raises
"weight_kg is not a number: abc".

**The lesson:** catching an exception and printing `str(exc)` is not error
handling, it is forwarding. Any message a user will read needs to be written
deliberately, with the field name in it.
