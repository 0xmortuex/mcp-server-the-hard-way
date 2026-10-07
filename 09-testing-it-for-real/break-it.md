# Break It: Testing

## Challenge 1: Remove the Split

Change the fake stub's `_reply` to send the whole packet in one `sendall`. Then
introduce a classic bug in the client: replace its `_read_packet` loop with a single
`recv` that assumes the packet is complete. Do the tests still pass?

**Hint:** With one `sendall` on localhost, they probably do, and that's the problem.
The bug is still there; you've just stopped exercising it. A test double should be
*harder* to talk to than the real thing, not easier. Put the split back and watch the
broken client fail.

## Challenge 2: Accept Everything

Delete the checksum comparison in `Client._read_packet`, so every packet is accepted.
Which assertion fails, and on which side (client or stub)?

**Hint:** The stub's `assert verdict == b"-"` fails, because the stub received `+` for a
packet it deliberately corrupted. This is why the fake stub checks the client's
behaviour, not only its return value. A client that drops bad data silently is broken
even if the next value it returns happens to be right.

## Challenge 3: Rebuild the Fixture

Install `ziglang`, change nothing in `kernel.c`, and run `kernel/build.py` with a
different Zig version than the committed binary was built with. Diff `nm`-style symbol
addresses (`python -c "from elftools..."`) between the old and new `kernel.elf`. Then
run `python verify.py`.

**Hint:** If any address moved, every test that hard-codes one would fail, even though
nothing is wrong. Notice that the `main.py` files look symbols up by name
(`address_of(funcs, "kmain")`) instead of hard-coding `0x1000a0`. That's what lets the
fixture be rebuilt at all.

## Challenge 4: Make a Test Flaky

In Module 08's `main.py`, remove the loop that waits for `"Triple fault"` to appear in
the log, and read the file once, immediately after `W00`. Run `python verify.py 08` 20
times in a row. Does it ever fail?

**Hint:** QEMU writes the log and closes the socket at almost the same moment, with no
ordering guarantee between them. A test that usually passes is worse than one that
always fails, because people learn to re-run it. Wait for the condition you need, with a
deadline, instead of assuming an order.

## Challenge 5: Find Bug Number Five

The four bugs in this README were all found by running against real data. Find a fifth
one. Point gdbstub-mcp at a different kernel (MortOS, Linux's `vmlinux`, or an
`x86_64` build of the fixture) and compare every tool's output with what you know is true.

**Hint:** Good places to look:
- symbols with `st_size == 0` (assembly labels), which `symbolize` treats as covering
  everything up to the next symbol
- DWARF 5 file numbering, which is 0-based where DWARF 4 is 1-based
- 64-bit `target.xml` layouts, where `g` doesn't include every register

When you find one, add it as a test first, then fix it.
