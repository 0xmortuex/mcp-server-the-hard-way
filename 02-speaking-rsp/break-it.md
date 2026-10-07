# Break It: The Wire Protocol

## Challenge 1: Send a Bad Checksum

Change `frame()` to always write `#00` and run `main.py`. What does the trace show? How
many times does `request()` retry before giving up?

**Hint:** The stub answers a bad checksum with `-`, and the client sends the packet
again. With a broken `frame()` every retry fails the same way, so the loop hits its limit
of five and raises. Without that limit, a client and stub can bounce `-` back and forth
forever. Every retry loop needs a bound.

## Challenge 2: Forget to Ack

Delete the `self._send_raw(b"+")` line in `_read_packet` and run again. The first reply
still arrives, so what goes wrong, and when?

**Hint:** In ack mode the stub waits for your `+` before it considers the reply
delivered. Without it, some stubs resend the same reply after a timeout. Your next read
then picks up the stale duplicate instead of the answer to your next question. Bugs like
this show up one request later than their cause, which makes them hard to trace.

## Challenge 3: Split a Packet

Make `_recv_more` read only 7 bytes at a time: `self.sock.recv(7)`. Does everything still
work? Now break `_read_packet` so it returns as soon as it sees `#`, without waiting for
the two checksum digits.

**Hint:** TCP is a byte stream, not a message stream. A packet can arrive across any
number of `recv()` calls. The `len(self.buf) >= end + 3` check is what makes the reader
correct. Remove it, and the code passes on localhost (where packets usually arrive
whole) but fails on a slow link.

## Challenge 4: Decode Run-Length by Hand

QEMU sends `0*"` in a reply. What bytes does it decode to? What about `f*!`? Check
your answers against `unescape()`.

**Hint:** `"` is 34, so 34 − 29 = 5 extra copies: `000000` (six zeros). `!` is 33,
so 4 extra copies: `fffff`. The count is always *extra* copies of the byte before `*`,
which is easy to get off by one.

## Challenge 5: Treat Empty as Error

Change `request()` to raise when the reply is empty. Which line of `main.py` fails now?
Which real situation would break in a debugger built this way?

**Hint:** Empty means "unsupported", and it's a normal answer when probing for
features. A debugger that asks whether hardware watchpoints exist (`Z2`) needs to hear
"no" and fall back, not crash. Keep the three outcomes separate: data, `Exx`, and empty.

→ [Next: Module 03](../03-registers-from-xml/README.md)
