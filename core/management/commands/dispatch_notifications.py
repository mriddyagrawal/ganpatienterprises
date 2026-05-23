"""
Pick up queued Notification rows and try to send them.

Run from cron / systemd timer every minute:

    * * * * * cd /srv/ganpati && uv run python manage.py dispatch_notifications

This is **single-dispatcher safe** as written — one cron, one process at
a time. The query is a plain filter, no row lock. Before scaling to a
second dispatcher (or a buggy double-cron), wrap the per-row claim in
`select_for_update(skip_locked=True)` so two runs can't pick the same
QUEUED row and double-send.

Retry chain (PLAN §6, Phase 6):
- On SENT: mark the row status=sent, set provider_message_id.
- On FAILED: mark the row status=failed, set error, then create a NEW
  Notification row with previous_attempt=that.id, attempt_number+1, and
  send_after = now + backoff[attempt_number-1]. After the schedule is
  exhausted, mark the new row status=abandoned immediately so the chain
  doesn't keep retrying forever.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Notification
from core.notifications import get_provider


class Command(BaseCommand):
    help = "Dispatch queued notifications. Run from cron every minute."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="Maximum number of notifications to attempt this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be sent without calling the provider.",
        )

    def handle(self, *args, **opts):
        limit = opts["limit"]
        dry_run = opts["dry_run"]
        provider = get_provider() if not dry_run else None

        now = timezone.now()
        # Claim with a single ORM query — small N, single process at first.
        # The DB row-locking matters once we have multiple dispatchers.
        ready = Notification.objects.filter(
            status=Notification.Status.QUEUED,
            send_after__lte=now,
        ).order_by("send_after", "pk")[:limit]

        sent = failed = abandoned = 0
        for n in ready:
            if dry_run:
                self.stdout.write(
                    f"  would dispatch #{n.pk} {n.kind} via {n.channel} "
                    f"→ {n.address} (attempt {n.attempt_number})"
                )
                continue
            outcome_kind = self._dispatch_one(n, provider)
            if outcome_kind == "sent":
                sent += 1
            elif outcome_kind == "abandoned":
                abandoned += 1
            else:
                failed += 1

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"[dry-run] {ready.count()} notification(s) would be attempted."
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"dispatch_notifications: sent={sent} failed={failed} abandoned={abandoned}"
        ))

    def _dispatch_one(self, n: Notification, provider) -> str:
        """Attempt one notification; on failure, enqueue the next retry.

        Returns one of "sent" / "failed" / "abandoned". Wrapped in a
        transaction so the row update + retry-row insert are atomic.
        """
        from core.notifications import SendOutcome, SendResult
        # Defense-in-depth: both shipped providers promise not to raise,
        # but a future provider with a bug shouldn't kill the whole
        # batch. Convert any unexpected exception to a FAILED result so
        # the retry chain takes over instead of the loop crashing.
        try:
            result = provider.send(address=n.address, body=n.body)
        except Exception as e:
            result = SendResult(
                outcome=SendOutcome.FAILED,
                error=f"provider raised: {e!r}",
            )
        with transaction.atomic():
            n.attempted_at = timezone.now()
            if result.outcome == "sent":
                n.status = Notification.Status.SENT
                n.provider_message_id = result.provider_message_id
                n.error = ""
                n.save(update_fields=[
                    "status", "provider_message_id", "error", "attempted_at",
                ])
                return "sent"

            # Failed.
            n.status = Notification.Status.FAILED
            n.error = result.error
            n.save(update_fields=["status", "error", "attempted_at"])

            # Schedule the next attempt — or abandon.
            schedule = settings.NOTIFICATION_RETRY_BACKOFF_SECONDS
            next_idx = n.attempt_number - 1  # 0-based into the schedule
            if next_idx >= len(schedule):
                # Out of retries. The chain ends with an ABANDONED marker so
                # operators can search for it without false-positive on
                # "merely failed last attempt, retry pending."
                Notification.objects.create(
                    payment=n.payment,
                    kind=n.kind,
                    channel=n.channel,
                    address=n.address,
                    body=n.body,
                    status=Notification.Status.ABANDONED,
                    provider_message_id="",
                    error="retry schedule exhausted",
                    previous_attempt=n,
                    attempt_number=n.attempt_number + 1,
                    send_after=timezone.now(),
                )
                return "abandoned"

            backoff = schedule[next_idx]
            Notification.objects.create(
                payment=n.payment,
                kind=n.kind,
                channel=n.channel,
                address=n.address,
                body=n.body,
                status=Notification.Status.QUEUED,
                previous_attempt=n,
                attempt_number=n.attempt_number + 1,
                send_after=timezone.now() + timedelta(seconds=backoff),
            )
            return "failed"
