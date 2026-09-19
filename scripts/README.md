# Differential-check scripts

`gcloud` equivalents of what the tool does, for cross-checking results.

The point is to catch a wrong CAI query — the kind of bug where the tool
returns a plausible-looking result set that is quietly missing rows, or
filtering client-side when it should be filtering server-side. A test with a
faked client cannot catch that, because the fake answers whatever the query
asked. Only a second implementation against the real API can.

**Practice: every new capability ships with a `gcloud` equivalent here.** This
matters most from Phase 2 onward, where `--label` and `--location` compile into
CAI query syntax and a subtly wrong filter string is easy to miss.

## Usage

```bash
./scripts/compare-resources.sh projects/my-project bucket
```

Both sides need `gcloud auth application-default login` and Cloud Asset Viewer
on the scope.
