(stereo-mode)=
# Stereo mode

**Stereo mode** is a two-node high-availability topology for Charmed PostgreSQL. It runs the database on **two `postgresql` units** and adds a separate, lightweight **`postgresql-watcher`** application that contributes a third vote to the cluster's Raft quorum. The watcher runs no PostgreSQL of its own — its only job is to make the cluster's vote count odd, so that high availability works with only two database copies.

```{figure} stereo-mode.png
:width: 690
:alt: Architecture diagram of a two-unit postgresql cluster with a separate postgresql-watcher node joined to the same Raft quorum over port 2222.

A stereo-mode deployment: two `postgresql` units plus a `postgresql-watcher` that contributes the third Raft vote on port 2222.
```

## Why a third vote is needed

Charmed PostgreSQL uses [Patroni](https://patroni.readthedocs.io/en/latest/) for high availability, and Patroni elects a primary through [Raft](https://raft.github.io/) consensus. Raft requires a **majority** of voters to agree before it can elect a primary.

With an even number of voters, a single failure splits the cluster in half and neither half holds a majority:

* **2 voters, 1 lost → 1 remaining.** One vote is not a majority of two, so the survivor cannot safely promote itself. Automatic failover stalls.

Adding a third voter restores a safe majority:

* **3 voters, 1 lost → 2 remaining.** Two votes are a majority of three, so the cluster keeps operating and can fail over automatically.

Running a third full PostgreSQL unit is the most robust option, but it adds a third full copy of the data — 50% more storage and compute — for a workload that only needs two copies. Stereo mode is the middle ground: two real database units plus one tiny watcher.

## How the watcher participates

The `postgresql-watcher` charm is deployed as a separate Juju application and integrated with `postgresql` over the `watcher-offer` endpoint (provided by `postgresql`) and the `watcher` endpoint (required by the watcher).

Once integrated, the watcher:

* Runs only Patroni's `patroni-raft-controller` from the [`charmed-postgresql` snap](https://snapcraft.io/charmed-postgresql), started as a systemd service and listening on port **2222**. It does **not** run PostgreSQL.
* Joins the cluster's Raft quorum as a voting member, so two `postgresql` units plus the watcher make three voters.

```{note}
The watcher participates in **leader election only**. It stores no table data and never becomes a PostgreSQL primary or replica.
```

## When the watcher votes (and when it doesn't)

The watcher runs and votes only when the `postgresql` units would otherwise form an even voter count. When they already make a safe odd count on their own, the watcher stops its Raft service and leaves the quorum:

* **An even number of units (2, 4, 6, …):** the watcher runs and votes, making the total odd — three voters for two units, five for four — so a single failure still leaves a clear majority.
* **An odd number of units, three or more (3, 5, 7, …):** the watcher stops itself and leaves the quorum, because the `postgresql` units already form an odd voter count — keeping it would make the count even again and degrade partition tolerance.
* **A single unit:** the watcher still runs and votes, but a one-unit cluster has no high availability either way — there is no second copy to fail over to.

This means you can keep the watcher integrated as you scale `postgresql` up and down: it stops itself only when the cluster is already an odd size of three or more, and runs otherwise.

## Availability-zone placement

A watcher in the same availability zone (AZ) as a database unit provides little extra protection, so the watcher charm checks its placement once integrated and re-checks it as the deployment changes:

* With `profile=production`, the watcher **blocks** if it shares an AZ with a PostgreSQL unit.
* With `profile=testing`, it only **warns**, which is convenient for local or single-AZ test setups.

## See also

* {ref}`deploy-stereo-mode` — deploy a stereo-mode cluster step by step.
* {ref}`architecture` — the standard Charmed PostgreSQL architecture.
* {ref}`scale-replicas` — scaling a standard cluster up and down.
