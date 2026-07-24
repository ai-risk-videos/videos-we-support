# Writing bench

Answers one question: **after a change, is the writing actually better?**

```bash
./bench.sh --label "what I just changed"
```

That runs the fixed golden set against the live backend, compares it to the previous run, and opens
a before/after page in the browser. Takes about 4 minutes.

## The loop

1. You give feedback ("the last sentence is too abstract", "grade 4 is too low").
2. The feedback becomes a row in `checks.py` so it is measured from now on.
3. Someone changes the prompts.
4. `./bench.sh` shows you both: the violation count for every rule, before and after, and the same
   fixed ideas rewritten side by side.

The point of step 2 is that nothing regresses silently. A rule you gave weeks ago keeps being
checked on every run, so a later change cannot quietly undo it.

## What is in a run

- **Channels** go through `/custom` with `fresh=true`, the same path the app uses. This measures
  real end-to-end output.
- **Concepts** go through `/writeoff`, which hands the model a fixed idea and asks it to write that
  idea. Same input every run, so the only thing that varies is the writing. This is the tightest
  before/after signal and it needs `EVENTS_KEY` (from Railway) in the environment.

Both halves are defined in `golden.json`. Change those inputs rarely: if the inputs move, the
comparison stops being apples to apples.

## Files

| file | what it does |
| --- | --- |
| `golden.json` | the fixed channels and fixed idea concepts |
| `checks.py` | every piece of feedback, as an executable check |
| `bench.py` | runs the golden set, saves a timestamped snapshot to `runs/` |
| `report.py` | diffs the two newest snapshots into `~/Downloads/bench-before-after.html` |
| `runs/*.json` | every snapshot, kept forever so any two points can be compared |

## Adding a new check

Write the function, then add one line to `CHECKS` in `checks.py`:

```python
("key", "Short rule name", "the feedback it came from", "idea", _my_check)
```

`scope="idea"` gets one idea and returns `None` or a reason string. `scope="batch"` gets the whole
list and returns `[(index, reason), ...]`, which is how cadence rules work (any rule about *how
often* something appears has to see the whole batch).

## Comparing two specific runs

```bash
python3 bench/report.py bench/runs/A.json bench/runs/B.json
```

## Notes

- Snapshots are append-only. A run never overwrites an older one.
- `EVENTS_KEY` must never be committed. `.gitignore` already covers `.eventskey`.
- Flesch-Kincaid grade is only applied to summaries. It is meaningless on a six-word hook and will
  even go negative, so a low hook score is not a defect.
