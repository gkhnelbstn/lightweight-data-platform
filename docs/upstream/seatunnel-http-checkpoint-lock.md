# For apache/seatunnel: the Http source sleeps holding the checkpoint lock

Draft of a bug report to file upstream. Nothing in it is specific to this
platform; the patch we carry is in `deploy/Dockerfile.seatunnel` and the
measurement is in issue #126.

---

**Title:** `[Bug] [Connector-V2] Http source: a streaming job's checkpoint
never completes, because `poll_interval_millis` is waited out holding the
checkpoint lock

**Version:** 2.3.13, and `dev` at the time of writing has the same code.

## What happens

An `Http` source with `poll_interval_millis` set (so the job is streaming)
fails after a few minutes with:

```
org.apache.seatunnel.engine.server.checkpoint.CheckpointException:
Checkpoint expired before completing. Please increase checkpoint timeout in
the seatunnel.yaml or jobConfig env.
  at CheckpointCoordinator.handleCoordinatorError(CheckpointCoordinator.java:289)
```

The reader is alive throughout: with a ten-second poll of two records,
`TableSourceReceivedCount` grew by four rows every 25 s right up to the
failure. It is the checkpoint that never completes, not the source that
stops.

Raising `checkpoint.interval` and `checkpoint.timeout` does not help. Tried,
with a ten-second poll:

| `checkpoint.interval` | `checkpoint.timeout` | result |
|---|---|---|
| 5 000 ms | 60 000 (server default) | FAILED, ~2 min |
| 20 000 ms | 60 000 (server default) | FAILED, ~2 min |
| 20 000 ms | 60 000 stated in the job env | FAILED, ~2 min |

## Why

`HttpSourceReader.pollNext` takes the checkpoint lock around the whole poll,
and the wait between listings is inside it:

```java
    @Override
    public void pollNext(Collector<SeaTunnelRow> output) throws Exception {
        synchronized (output.getCheckpointLock()) {
            internalPollNext(output);
        }
    }

    @Override
    public void internalPollNext(Collector<SeaTunnelRow> output) throws Exception {
        try {
            ...
        } finally {
            if (Boundedness.BOUNDED.equals(context.getBoundedness()) && noMoreElementFlag) {
                context.signalNoMoreElement();
            } else {
                if (httpParameter.getPollIntervalMillis() > 0) {
                    Thread.sleep(httpParameter.getPollIntervalMillis());   // lock still held
                }
            }
        }
    }
```

That is the lock `SourceFlowLifeCycle#triggerBarrier` needs, as the engine's
own comment in `SourceFlowLifeCycle` says:

> The current thread obtain a checkpoint lock in the method
> `SourceReader#pollNext(Collector)`. When trigger the checkpoint or
> savepoint, other threads try to obtain the lock in the method
> `SourceFlowLifeCycle#triggerBarrier(Barrier)`. When high CPU load,
> checkpoint process may be blocked as long time. So we need sleep to free
> the CPU.

The engine adds a `Thread.sleep(0L)` between polls for exactly this reason.
The Http reader turns the window between two polls into one narrow window
every `poll_interval_millis`, and `synchronized` is not fair, so the barrier
thread can lose the race indefinitely. A longer timeout does not help: this is
a contended lock, not a slow operation.

## Suggested fix

`Object.wait(long)` releases the monitor while it waits; `Thread.sleep` does
not. The monitor is held at that point, so this needs no other change, and a
spurious wake-up only means polling slightly early:

```diff
-                    Thread.sleep(httpParameter.getPollIntervalMillis());
+                    output.getCheckpointLock().wait(httpParameter.getPollIntervalMillis());
```

With this applied the same job checkpoints normally and has been running
without interruption, delivering every poll, instead of failing at about two
minutes.

The two `Thread.sleep(10)` calls in the pagination loops are inside the same
lock and are small enough not to matter, but they are the same shape if
anyone wants to tidy them.
