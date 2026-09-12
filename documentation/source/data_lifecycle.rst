Data lifecycle and recovery
===========================

Status
------

This is Gambit's repository-owned lifecycle contract for persisted research.
Data/storage-owner approval and an exercise using the owner's real storage and
retention system are still required before production reliance. Gambit does not
operate an authoritative database or promise a time-based recovery objective.

Authority and ownership
-----------------------

Caller-owned, timestamped source data and its schema/revision identity are the
source of truth. A versioned :class:`~gambit.BacktestResult` bundle is an
immutable derived audit artifact. The caller owns storage location, access,
retention, deletion, legal holds, backup media, and restoration scheduling.
Never put proprietary market data or user result bundles in the Gambit source
repository.

The core/persistence owner controls the ``gambit.backtest-result`` manifest.
Writers emit version 4; the bounded reader supports versions 2, 3, and 4.
Migration means loading a supported old bundle and saving a distinct version-4
bundle. Missing historical fields stay absent, and the old bundle is never
overwritten. Unsupported versions require a reviewed converter or a rerun from
retained source inputs.

The native factor store is an experimental, disposable cache. Its generation
manifest is version 1 and current mapped segments are version 3. The explicit
``gambit-factor-cache migrate`` command handles supported version-1/2 segments,
is dry-run first, and publishes replacements without editing old generations.
The factor/cache owner controls that format while it remains experimental.

Recovery objectives
-------------------

The repository target is:

* RPO: the last externally retained source input or independently retained,
  verified result bundle.
* RTO: the time the caller needs to restore that bundle or rerun from retained
  inputs. Gambit makes no fixed duration claim.

At least one backup copy needed for a retention promise must be outside the
primary result location. Retain source inputs, schema/revision identifiers,
configuration, callback/model revisions, and the Gambit commit for at least as
long as any dependent result or decision must remain reproducible. Apply the
historical-output disposition policy before treating pre-correction results as
current evidence.

Result-bundle publication and backup
------------------------------------

``BacktestResult.save`` creates a sibling staging directory, flushes every IPC
member and the manifest, then atomically renames the complete directory. It
refuses to replace an existing destination. An ordinary exception removes its
staging directory; abrupt process or host loss before rename can leave a hidden
``.<destination>.*`` directory, but must not expose a partial destination.

For every result selected for retention:

#. Load the source with :meth:`gambit.BacktestResult.load` under the limits used
   by the intended consumer. Record its run fingerprint and bundle format.
#. Copy the immutable directory to a new backup location without modifying the
   source. Do not copy a staging directory.
#. Load the backup and require the same run fingerprint before recording the
   backup as valid. A filesystem copy exit code alone is insufficient evidence.
#. Restore to a new, absent destination, load it again, and reconcile the run
   fingerprint and required tables before using it. Never overwrite or repair
   the original in place.

After a crash, first confirm that no writer is active and that the intended
destination is absent or independently loads successfully. Hidden sibling
staging directories may then be quarantined or removed. If the destination
exists but fails validation, preserve it as a labeled corrupt artifact when
required for audit, restore a verified copy to a new path, or rerun. Do not edit
checksums, manifests, or Arrow tables to make validation pass.

Factor-cache recovery
---------------------

Factor generations are immutable and publication is serialized. ``CURRENT``
and per-node pointers change only after a staged generation is complete. The
collector removes unreferenced staging/generation state while preserving active
leases; migration checkpoints are advisory and node pointers are authoritative.

Do not back up a factor cache as authoritative data. On corruption, failed
migration, lost storage, or an incompatible format, retain diagnostics if
needed, discard the cache, and rebuild it from the authoritative inputs and
factor identities. Use CLI ``inventory`` and ``health`` before mutation,
``migrate`` or ``collect`` in preview mode, and ``--apply`` only after reviewing
the plan. Full-disk and permission failures must fail the cache operation; they
must not be converted into a successful research result.

Deletion and evidence
---------------------

Delete a result only after its owner confirms that the retention period, legal
holds, downstream references, historical-output disposition, and required
backup copies permit deletion. Cache eviction follows its configured quota and
lease rules and has no authority to delete source inputs or result bundles.

``tests/test_data_lifecycle.py`` exercises verified backup/restore, corruption
fallback, and abrupt publication death. Existing result tests cover version-2/3
read-and-resave behavior, while factor-store tests cover version-1/2-to-3
migration and process death at column, manifest, generation, and pointer stages.
These synthetic filesystem drills establish the library contract, not the
owner's storage durability, elapsed RTO, retention approval, or backup inventory.
