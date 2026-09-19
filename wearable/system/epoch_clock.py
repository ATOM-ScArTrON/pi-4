"""
epoch_clock.py
--------------
EpochClock: GPS-synced epoch clock with offline monotonic fallback.

Implements §3.5 of the PUC Two-Tier Mesh Security Architecture spec.

Primary source: GPS UTC timestamp when has_fix is True.
Fallback (No-GPS / Degraded Clock): epoch_start_time + elapsed runtime
(time.monotonic()) with a widened window [epoch-1, epoch, epoch+1] to
absorb crystal clock drift.

Epoch duration: 3600 seconds (1 hour).
"""

import time

EPOCH_DURATION = 3600  # seconds per epoch (1 hour)


class EpochClock:
    """Provides the current epoch number for the mesh security layer.

    Parameters
    ----------
    epoch_start_time : float | int
        Absolute UTC timestamp (seconds since the Unix epoch) marking the
        start of epoch 0.  The server returns this value during provisioning
        as ``epoch_start_time``.
    gps : GPSReceiver or None
        Optional GPS receiver.  When provided and ``gps.has_fix`` is True
        the clock derives the epoch from ``gps.utc_time`` (a float UTC
        timestamp). When absent or unfixed, falls back to monotonic runtime.
    """

    def __init__(self, epoch_start_time: float, gps=None):
        self.epoch_start_time = float(epoch_start_time)
        self.gps = gps
        self._boot_monotonic = time.monotonic()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _epoch_from_utc(self, utc_timestamp: float) -> int:
        """Convert an absolute UTC timestamp to an epoch number."""
        return int(utc_timestamp // EPOCH_DURATION)

    def _epoch_from_monotonic(self) -> int:
        """Estimate the current epoch using monotonic runtime (degraded mode)."""
        elapsed = time.monotonic() - self._boot_monotonic
        estimated_utc = self.epoch_start_time + elapsed
        return int(estimated_utc // EPOCH_DURATION)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_epoch(self) -> tuple[int, bool]:
        """Return ``(epoch_number, gps_synced)``.

        ``gps_synced`` is True when the GPS has a valid fix and a UTC
        timestamp, False when falling back to the monotonic clock.
        """
        if self.gps is not None and getattr(self.gps, "has_fix", False):
            utc_time = getattr(self.gps, "utc_time", None)
            if utc_time is not None:
                try:
                    return (self._epoch_from_utc(float(utc_time)), True)
                except (TypeError, ValueError):
                    pass  # fall through to degraded mode
        return (self._epoch_from_monotonic(), False)

    def get_active_epochs(self) -> list[int]:
        """Return the list of epoch numbers that should be considered valid
        for incoming pseudo-ID table lookups.

        When GPS-synced: ``[epoch, epoch + 1]`` (tight window).
        When degraded (no LOS):``[epoch - 1, epoch, epoch + 1]`` to absorb
        crystal clock drift.
        """
        epoch, gps_synced = self.get_epoch()
        if gps_synced:
            return [epoch, epoch + 1]
        return [epoch - 1, epoch, epoch + 1]
