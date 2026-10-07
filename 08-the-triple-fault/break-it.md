# Break It: The Triple Fault

## Challenge 1: A Fault That Wasn't Fatal

Install a working #UD handler (or, without touching the kernel, stop at `triple_fault`
and skip the `lidt` by setting EIP past it), so the `ud2` is handled. Then trigger a
*second*, fatal crash later. Your log now has an old #UD that was handled fine, followed
by the real chain. What does `explain()` blame?

**Hint:** `events[0]` is the old, handled #UD, so the output is wrong. The real
gdbstub-mcp walks backward from the last `#DF` through consecutive sequence numbers
(`152, 153, 154`), because the chain that killed the machine has no gaps. Implement that
walk and check that the handled fault is no longer named.

## Challenge 2: Drowning in Timer Ticks

Remove the `vector >= 0x20` filter and run the demo again. How many "exceptions" does it
report? Now boot the kernel for 30 seconds before triggering the crash and count again.

**Hint:** The timer fires about 18 times a second, and each tick is a full event with a
register dump. An agent handed the raw log would be reading thousands of lines to find
three. Filtering is the main value this tool adds, so don't skip it.

## Challenge 3: A Page Fault

Make the kernel dereference `*(volatile int*)0x8 = 1` with paging enabled and a missing
#PF handler. Decode the error code and CR2 yourself before running the parser. Does your
decoding match `decode_error()`?

**Hint:** QEMU appends `CR2=00000008` to a #PF event line instead of
`env->regs[R_EAX]`. Error code `0002` means not present, write, kernel mode. A CR2 below
`0x1000` should make your explanation say "NULL pointer". The full gdbstub-mcp has this
hint, and this module's parser needs extending to read CR2.

## Challenge 4: Lie in the Error Code

The parser trusts `e=`. Construct a log by hand where a #GP has error code `0x0000` and
the chain still ends in a triple fault. What does the "escalated because" line say?

**Hint:** Nothing. With no IDT bit set, the parser can't name a missing entry, so it
must not invent one. A #GP with code 0 has several possible causes: a privileged
instruction, a non-canonical address, or a bad segment. A good explanation says that,
instead of guessing which one.

## Challenge 5: Forget `-no-reboot`

Run the demo without `-no-reboot` in the QEMU command. What does the stub send instead
of `W00`? What does the log look like after 10 seconds?

**Hint:** QEMU resets and boots the kernel again, so the debugger never sees an exit.
The log fills with boot after boot, each ending in the same triple fault. The parser
still works, but the "last chain" logic now matters, and `debug_continue` would time
out and report "Still running", which is technically true.

→ [Next: Module 09](../09-testing-it-for-real/README.md)
