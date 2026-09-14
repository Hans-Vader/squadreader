# Why the reader is privileged

`cap_drop: [ALL]` plus exactly two capabilities:

- `SYS_PTRACE` — `/proc/<pid>/maps` is mode 0444 but gated by the ptrace check;
- `DAC_READ_SEARCH` — `/proc/<pid>/mem` is mode 0600 and owned by the game's
  user, so the DAC check applies on top.

Both are needed. Docker's default capability set appears to work with only
`SYS_PTRACE`, but only because it still carries `DAC_OVERRIDE`. The reader is
still read-only: it never opens the game's memory for writing.

Installing the reader *into* the Squad image does not avoid this. It would be a
sibling of the game process rather than an ancestor, and `ptrace_scope=1` grants
attach to ancestors only — the same capability, plus a forked image and two
lifecycles behind one PID 1.
