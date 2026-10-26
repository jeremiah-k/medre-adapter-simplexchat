# Retain Linux profile fencing after bridge process loss

Use a Linux file-description lock shared by the Python profile owner and
its spawned native worker. A crash or forced termination of the bridge no
longer releases profile admission while the worker retains its inherited
lock descriptor. The SQLite profile lease continues to own normal process
contention and namespace state. Non-Linux systems retain their existing
lease behavior. The Linux guarantee does not extend to independent native
descendants that close inherited descriptors; qualify those process trees
separately before claiming comprehensive containment.

The Linux crash probe owns orphan reaping through an isolated subreaper and
checks that admission succeeds after the worker exits. A separate inherited
worker probe verifies that closing the bridge's lease preserves live fencing.
