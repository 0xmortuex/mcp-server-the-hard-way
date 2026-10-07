# Break It: The First Server

## Challenge 1: Print to Stdout

Add `print("pinging", port)` at the top of `ping_stub` and run the demo again. What does
the client report? Now change it to `print("pinging", port, file=sys.stderr)`.

**Hint:** In `--serve` mode, stdout carries JSON-RPC messages. Your print injects a line
that isn't JSON into that stream. Depending on the SDK version, the client skips it,
errors on it, or desynchronizes. Stderr is free for logs. Claude Code shows a server's
stderr in its MCP logs.

## Challenge 2: A Port That Never Answers

Point `ping_stub` at an address that drops packets instead of refusing them, such as a
non-routable IP like `10.255.255.1`. How long does the call take? Now remove the
`timeout=3` argument.

**Hint:** A refused connection fails instantly. A filtered one waits for the OS TCP
timeout, which can be over a minute. Meanwhile the agent is stuck waiting on a tool that
never returns. Every tool that touches the network or a process needs a bound on how long
it can take.

## Challenge 3: Raise Instead of Return

Change the failure branch to `raise ConnectionError(...)` instead of returning the
`UNREACHABLE` string. Run the demo and inspect `result.is_error` and `result.content`.

**Hint:** The SDK catches the exception and returns it as an error result, so the agent
still sees the message. That's fine for genuine errors. But "the stub isn't up yet" is
an expected state the agent can act on, not a crash. Decide deliberately which is which;
Module 07 comes back to this.

## Challenge 4: A Docstring the Model Can't Use

Replace the docstring with `"""Ping."""` and look at what `tools/list` returns. Then ask
yourself: given only that, would an agent know to call this *before* trying to debug,
or what to do when it says UNREACHABLE?

**Hint:** The description is the only documentation the model gets. "Ping" tells it
nothing about QEMU, ports or the `-s` flag. Good tool descriptions say when to use the
tool, what the arguments mean, and how to interpret the result.

## Challenge 5: Two Debuggers at Once

Attach a real debugger (or a second `nc localhost 1234`) to QEMU, and keep it connected.
Now run `ping_stub`. Does it still say REACHABLE?

**Hint:** QEMU's gdbstub serves one client at a time. Depending on the version, a second
connection is refused, accepted and ignored, or replaces the first. "The port accepted a
TCP connection" isn't the same as "you can debug through it". Module 02 checks that the
thing on the other end actually speaks RSP.

→ [Next: Module 02](../02-speaking-rsp/README.md)
