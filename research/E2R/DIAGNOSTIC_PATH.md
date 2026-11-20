# Construct a diagnostic path

The working directory contains the Pylint source from the supplied buggy
`base_commit`, with the reference test patch applied. The case input gives you
the entry test selector and a supplied root-cause candidate.

1. Read the selected test, its relevant inputs, fixtures, and assertion. For a
   parametrized selector, focus on the selected case.
2. Follow the test into production code. Inspect the actual calls, callback
   registrations, control decisions, or data dependencies that connect the test
   to the supplied location. Follow the branch that the test input takes.
3. Report the shortest chain you can support in order from the entry test to
   the root cause. For every step, give its repository-relative file, symbol,
   and line. For every link, explain what is called, selected, or passed across
   the boundary and cite the source line that establishes the link.

Use the source in this directory. Keep it read-only and stay within the
supplied checkout. A focused test or probe is optional when the local runtime
supports it. Use `runtime` evidence only for a link you actually observe and
include the command and observation in the link explanation. Use `source` for
links inferred from reading code.

Return the structured result requested by the runner:

- Use `connected` when the supported chain reaches a supplied root-cause
  location. If the evidence supports only a prefix, use `partial` and explain
  what bridge is missing. Use `unresolved` when you cannot establish a useful
  prefix or when the candidate conflicts with the selected test.
- Put the entry test first in `steps` and set its `link_from_previous` to null.
  For every subsequent step, give the incoming link, its evidence kind, and a
  file and line for the link. Keep the chain contiguous. Use an empty array
  when you have no supported steps.
- Use `summary` to explain how the reached operation relates to the test
  assertion, or where the chain breaks. Mark behavioral claims based on source
  reasoning as inferences.
