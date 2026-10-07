# Break It: Register Discovery

## Challenge 1: Boot x86-64

Run `main.py` against `qemu-system-x86_64` instead, with any image, or just `-S` and no
kernel at all. Which assertions fail? Which parts of the code worked without any change?

**Hint:** The architecture becomes `i386:x86-64`, EIP becomes RIP, and registers are 64
bits wide. The parsing, numbering and `g` splitting all adapt automatically, because they
never assumed a layout. Only the asserts that name `eip` and expect i386 fail. That's the
payoff of discovering instead of hard-coding.

## Challenge 2: Ignore `regnum`

Delete the two lines that honour the `regnum` attribute, so registers are numbered purely
by document order. Does the i386 output change? Write a small XML where `regnum` jumps
(e.g. `cr0` at 40 after eight registers) and compare `split_g` with and without the fix.

**Hint:** QEMU's i386 file numbers everything in order, so this bug hides there. On
targets whose XML jumps numbers (system registers placed at a fixed regnum), ignoring the
attribute shifts every later register. You'd read CR2 and get CR3, and nothing would tell
you.

## Challenge 3: A Chunk Size of 1

Set `chunk=1` in `read_xfer`. How many requests does reading `i386-32bit.xml` take now?
Then set it larger than `PacketSize`.

**Hint:** 7372 round trips for one file, all correct, all slow. That shows why chunking
exists. Going over `PacketSize` either gets truncated replies (still `m`, so the loop
copes) or an error, depending on the stub. Correct code must accept a reply shorter than
it asked for, which this loop does by using `len(out)` as the next offset.

## Challenge 4: Forget the Prolog

Remove the `re.sub` that strips `<?xml ...?>` from included files. What does
`ElementTree` say?

**Hint:** "XML or text declaration not at start of entity". Splicing documents together
by text is fragile. It works here because target descriptions are simple and the code
removes the two constructs that can't nest: the prolog and the doctype.

## Challenge 5: Big-Endian Targets

The code decodes every register with `"little"`. Find an architecture QEMU emulates as
big-endian (`qemu-system-ppc`, or `m68k`) and predict what `split_g` would print for a
register holding `0x00000001`.

**Hint:** `0x01000000`. The bytes are right but read backwards. gdbstub-mcp decides byte
order from the `<architecture>` string. Note that `mipsel` is little-endian while `mips`
is big-endian, so a simple prefix check isn't enough.

→ [Next: Module 04](../04-memory-breakpoints-and-time/README.md)
