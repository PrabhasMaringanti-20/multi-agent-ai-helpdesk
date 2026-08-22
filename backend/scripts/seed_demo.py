"""Enterprise demo seeder.

Idempotent. Seeds:
  * schema + roles + org 'acme' + one user per role
  * every category in the in-memory CategoryRegistry (8 canonical + 28 extended)
  * 93 realistic KB articles (scripts/demo_kb_data.py) as published documents
  * demo tickets (varied status/priority) with a user<->engineer message thread
  * notifications (unread + read) for the demo users
  * analytics events feeding the admin dashboard
  * a couple of AI chat conversations (chat history)

Run from backend/ with the app importable:
    PYTHONPATH=backend python scripts/seed_demo.py
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta

import asyncpg
from sqlalchemy import func, select

ROLES = [
    ("end_user", "End User"),
    ("support_engineer", "Support Engineer"),
    ("admin", "Administrator"),
    ("sme_reviewer", "SME Reviewer"),
]
USERS = [
    ("admin@acme.com", "Demo Admin", "admin"),
    ("engineer@acme.com", "Demo Engineer", "support_engineer"),
    ("sme@acme.com", "Demo SME Reviewer", "sme_reviewer"),
    ("user@acme.com", "Demo End User", "end_user"),
]
DEMO_PASSWORD = "ChangeMe123!"

NOW = datetime.now(UTC)


def _article_body(a: dict) -> str:
    """Compose one rich markdown chunk carrying every article field."""
    sym = "\n".join(f"- {s}" for s in a.get("symptoms", []))
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(a.get("steps", []), 1))
    rel = "\n".join(f"- {r}" for r in a.get("related_articles", []))
    perms = ", ".join(a.get("required_permissions", [])) or "Self-service"
    tags = ", ".join(a.get("tags", []))
    kw = ", ".join(a.get("confidence_keywords", []))
    return (
        f"# {a['title']}\n\n"
        f"**Category:** {a['category_key']}  |  **Estimated resolution time:** "
        f"{a.get('est_resolution_time', 'N/A')}  |  **Required permissions:** {perms}\n\n"
        f"## Problem\n{a['problem']}\n\n"
        f"## Symptoms\n{sym}\n\n"
        f"## Root Cause\n{a['root_cause']}\n\n"
        f"## Step-by-step Guided Solution\n{steps}\n\n"
        f"## Related Articles\n{rel}\n\n"
        f"{a.get('screenshot_placeholder', '')}\n\n"
        f"**Tags:** {tags}\n\n**Keywords:** {kw}\n"
    )


async def ensure_database() -> None:
    # Postgres opens its TCP port a moment before it can serve queries: during
    # startup / crash recovery it accepts the connection but rejects queries with
    # "the database system is starting up" (CannotConnectNowError, SQLSTATE 57P03).
    # Retry until it is genuinely ready (or the port is finally listening), ~30s.
    conn = None
    last_err: Exception | None = None
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            conn = await asyncpg.connect(
                user="postgres",
                password="postgres",
                host="localhost",
                port=5432,
                database="postgres",
            )
            break
        except (asyncpg.exceptions.CannotConnectNowError, OSError) as exc:
            last_err = exc
            await asyncio.sleep(1.0)
    if conn is None:
        raise RuntimeError(f"PostgreSQL not ready on localhost:5432 after 30s: {last_err}")
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname='helpdesk'"):
            await conn.execute("CREATE DATABASE helpdesk")
            print("created database 'helpdesk'")
    finally:
        await conn.close()


async def main() -> None:
    await ensure_database()

    from app.core.constants import (
        ConversationStatus,
        Decision,
        DocStatus,
        MessageRole,
        NotificationChannel,
        NotificationStatus,
        NotificationType,
        SourceType,
        TicketEventType,
        TicketPriority,
        TicketStatus,
    )
    from app.core.security import hash_password
    from app.db.base import Base
    from app.db.session import SessionFactory, engine
    from app.models.conversation import Conversation, Message
    from app.models.knowledge import KbChunk, KbDocument
    from app.models.ops import AnalyticsEvent, Notification
    from app.models.organization import Organization, Role
    from app.models.registry import CategoryRegistry as CategoryRow
    from app.models.ticket import Ticket, TicketEvent
    from app.models.user import User
    from app.registries.category_registry import get_category_registry
    from demo_kb_data import KB_ARTICLES

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionFactory() as s:
        # ---- roles ----
        for key, name in ROLES:
            if not (await s.execute(select(Role).where(Role.key == key))).scalar_one_or_none():
                s.add(Role(key=key, display_name=name))
        await s.flush()

        # ---- categories (all registry entries: 8 canonical + 28 extended) ----
        registry = get_category_registry()
        existing_cats = {r for (r,) in (await s.execute(select(CategoryRow.category_key))).all()}
        for key in registry:
            if key in existing_cats:
                continue
            c = registry.get(key)
            s.add(
                CategoryRow(
                    category_key=c.category_key,
                    display_name=c.display_name,
                    required_intake_fields=dict(c.required_intake_fields),
                    retrieval_namespace=c.retrieval_namespace,
                    sla_tier=c.sla_tier,
                    handoff_queue=c.handoff_queue,
                    thresholds=dict(c.thresholds),
                    tool_bindings={},
                    is_active=True,
                )
            )
        await s.flush()

        # ---- org + users ----
        org = (
            await s.execute(select(Organization).where(Organization.slug == "acme"))
        ).scalar_one_or_none()
        if org is None:
            org = Organization(name="Acme Corp", slug="acme", settings={}, is_active=True)
            s.add(org)
            await s.flush()
        roles_by_key = {r.key: r for r in (await s.execute(select(Role))).scalars().all()}
        for email, full_name, role_key in USERS:
            if not (
                await s.execute(select(User).where(User.org_id == org.id, User.email == email))
            ).scalar_one_or_none():
                s.add(
                    User(
                        org_id=org.id,
                        email=email,
                        hashed_password=hash_password(DEMO_PASSWORD),
                        full_name=full_name,
                        role_id=roles_by_key[role_key].id,
                        is_active=True,
                    )
                )
        await s.flush()
        users = {
            u.email: u
            for u in (await s.execute(select(User).where(User.org_id == org.id))).scalars().all()
        }
        admin, engineer, end_user = (
            users["admin@acme.com"],
            users["engineer@acme.com"],
            users["user@acme.com"],
        )

        # ---- KB articles (93) ----
        existing_titles = {
            t
            for (t,) in (
                await s.execute(select(KbDocument.title).where(KbDocument.org_id == org.id))
            ).all()
        }
        added_kb = 0
        for a in KB_ARTICLES:
            if a["title"] in existing_titles:
                continue
            body = _article_body(a)
            doc = KbDocument(
                org_id=org.id,
                title=a["title"],
                source_type=SourceType.MANUAL,
                category=a["category_key"],
                retrieval_namespace=a["category_key"],
                doc_status=DocStatus.PUBLISHED,
                version=1,
                checksum=hashlib.sha256(body.encode()).hexdigest(),
                created_by_user_id=admin.id,
                source_uri=f"seed://{a['category_key']}/{a['title']}",
                last_verified_at=NOW,
            )
            s.add(doc)
            await s.flush()
            s.add(
                KbChunk(
                    doc_id=doc.id,
                    org_id=org.id,
                    category_key=a["category_key"],
                    retrieval_namespace=a["category_key"],
                    chunk_index=0,
                    text=body,
                    embedding_model_id="seed",
                    doc_status=DocStatus.PUBLISHED,
                    version=1,
                    token_count=len(body.split()),
                    source_uri=doc.source_uri,
                    last_verified_at=NOW,
                )
            )
            added_kb += 1
        await s.flush()

        # ---- demo tickets (+ user<->engineer chat thread) ----
        _tkt_marker = (
            await s.execute(
                select(Ticket.id).where(
                    Ticket.org_id == org.id,
                    Ticket.subject == "GlobalProtect VPN error 800 on Windows 11",
                )
            )
        ).first()
        added_tickets = 0
        if _tkt_marker is None:
            demo_tickets = [
                (
                    "vpn",
                    "GlobalProtect VPN error 800 on Windows 11",
                    TicketPriority.HIGH,
                    TicketStatus.IN_PROGRESS,
                    True,
                    [
                        ("user", "Hi, my VPN still fails with error 800 after restarting."),
                        (
                            "engineer",
                            "Thanks — I can see repeated tunnel blocks. Are you on office Wi-Fi or home?",
                        ),
                        ("user", "Home Wi-Fi over a personal router."),
                        (
                            "engineer",
                            "Please switch to a wired connection and retry; I've bumped the firewall exception.",
                        ),
                    ],
                ),
                (
                    "printer",
                    "Cannot print to FLOOR3-HP printer",
                    TicketPriority.MEDIUM,
                    TicketStatus.OPEN,
                    True,
                    [
                        ("user", "Jobs sit in the queue and never print."),
                        (
                            "engineer",
                            "I'll clear the spooler on the print server and confirm the driver.",
                        ),
                    ],
                ),
                (
                    "outlook",
                    "Outlook stuck on 'Trying to connect'",
                    TicketPriority.MEDIUM,
                    TicketStatus.AWAITING_USER,
                    True,
                    [
                        (
                            "engineer",
                            "Can you confirm Outlook is on the latest build (File > Office Account)?",
                        )
                    ],
                ),
                (
                    "sap",
                    "SAP GUI logon 'No connection to message server'",
                    TicketPriority.HIGH,
                    TicketStatus.TRIAGED,
                    True,
                    [],
                ),
                (
                    "laptop_performance",
                    "Laptop extremely slow after Windows Update",
                    TicketPriority.LOW,
                    TicketStatus.RESOLVED,
                    True,
                    [
                        ("user", "It was pegged at 100% disk."),
                        (
                            "engineer",
                            "Disabled SysMain and cleared the update cache — resolved. Closing.",
                        ),
                    ],
                ),
                (
                    "access_request",
                    "Request access to Finance shared drive",
                    TicketPriority.LOW,
                    TicketStatus.OPEN,
                    False,
                    [],
                ),
                (
                    "mfa",
                    "Lost phone — cannot approve MFA",
                    TicketPriority.URGENT,
                    TicketStatus.IN_PROGRESS,
                    True,
                    [],
                ),
                (
                    "docker",
                    "Docker Desktop won't start (WSL2 backend)",
                    TicketPriority.MEDIUM,
                    TicketStatus.OPEN,
                    False,
                    [],
                ),
            ]
            for i, (cat, subject, prio, status, assign, thread) in enumerate(demo_tickets):
                created = NOW - timedelta(days=len(demo_tickets) - i, hours=i)
                conv = Conversation(
                    org_id=org.id,
                    user_id=end_user.id,
                    status=ConversationStatus.AWAITING_HUMAN,
                    category=cat,
                    title=subject,
                    last_message_at=created,
                )
                s.add(conv)
                await s.flush()
                q = registry.get(cat)
                tkt = Ticket(
                    org_id=org.id,
                    conversation_id=conv.id,
                    created_by_user_id=end_user.id,
                    assigned_engineer_id=engineer.id if assign else None,
                    category=cat,
                    priority=prio,
                    status=status,
                    assigned_queue=q.handoff_queue,
                    subject=subject,
                    intake_fields={"summary": subject},
                    escalation_reason="ai_unresolved",
                    redacted_transcript={},
                    final_confidence=0.42,
                )
                tkt.created_at = created
                s.add(tkt)
                await s.flush()
                s.add(
                    TicketEvent(
                        ticket_id=tkt.id,
                        actor_user_id=end_user.id,
                        event_type=TicketEventType.CREATED,
                        payload={"subject": subject},
                    )
                )
                if assign:
                    s.add(
                        TicketEvent(
                            ticket_id=tkt.id,
                            actor_user_id=engineer.id,
                            event_type=TicketEventType.ASSIGNED,
                            payload={"engineer": engineer.email},
                        )
                    )
                for j, (who, msg) in enumerate(thread):
                    ev = TicketEvent(
                        ticket_id=tkt.id,
                        actor_user_id=(end_user.id if who == "user" else engineer.id),
                        event_type=TicketEventType.COMMENTED,
                        payload={
                            "text": msg,
                            "sender_role": who,
                            "sender_email": (end_user.email if who == "user" else engineer.email),
                        },
                    )
                    ev.created_at = created + timedelta(minutes=5 * (j + 1))
                    s.add(ev)
                added_tickets += 1
            await s.flush()

        # ---- notifications ----
        _ntf_marker = (
            await s.execute(
                select(Notification.id).where(
                    Notification.org_id == org.id,
                    Notification.payload["title"].astext == "Ticket resolved",
                )
            )
        ).first()
        added_notifs = 0
        if _ntf_marker is None:
            notifs = [
                (
                    end_user,
                    NotificationType.RESOLVED,
                    "Ticket resolved",
                    "Your ticket 'Laptop extremely slow after Windows Update' was resolved.",
                    False,
                ),
                (
                    end_user,
                    NotificationType.MENTION,
                    "Engineer replied",
                    "Demo Engineer replied on 'GlobalProtect VPN error 800'.",
                    False,
                ),
                (
                    end_user,
                    NotificationType.MENTION,
                    "New message",
                    "You have a new message on 'Cannot print to FLOOR3-HP printer'.",
                    False,
                ),
                (
                    end_user,
                    NotificationType.TICKET_ASSIGNED,
                    "Ticket assigned",
                    "Your ticket 'Lost phone — cannot approve MFA' was assigned to Demo Engineer.",
                    True,
                ),
                (
                    engineer,
                    NotificationType.TICKET_ASSIGNED,
                    "Ticket assigned to you",
                    "Ticket 'GlobalProtect VPN error 800 on Windows 11' was assigned to you.",
                    False,
                ),
                (
                    engineer,
                    NotificationType.MENTION,
                    "New message",
                    "Demo End User replied on 'Cannot print to FLOOR3-HP printer'.",
                    False,
                ),
                (
                    engineer,
                    NotificationType.SLA_BREACH,
                    "SLA warning",
                    "Ticket 'Lost phone — cannot approve MFA' is approaching its SLA due time.",
                    False,
                ),
                (
                    admin,
                    NotificationType.APPROVAL_REQUEST,
                    "Knowledge updated",
                    "A knowledge article was updated and awaits review.",
                    False,
                ),
            ]
            for i, (usr, ntype, title, bodytext, read) in enumerate(notifs):
                n = Notification(
                    org_id=org.id,
                    recipient_user_id=usr.id,
                    channel=NotificationChannel.IN_APP,
                    type=ntype,
                    status=NotificationStatus.READ if read else NotificationStatus.SENT,
                    payload={"title": title, "body": bodytext},
                )
                n.created_at = NOW - timedelta(hours=i * 3)
                if read:
                    n.read_at = NOW
                s.add(n)
                added_notifs += 1
            await s.flush()

        # ---- analytics events (feed the admin dashboard) ----
        _evt_marker = (
            await s.execute(
                select(AnalyticsEvent.id).where(
                    AnalyticsEvent.org_id == org.id,
                    AnalyticsEvent.properties["seed"].astext == "demo",
                )
            )
        ).first()
        added_events = 0
        if _evt_marker is None:
            spread = [
                ("answer_delivered", 22),
                ("clarification_requested", 9),
                ("ticket_created", 8),
                ("chat_started", 30),
                ("ticket_resolved", 6),
                ("feedback_positive", 14),
                ("feedback_negative", 3),
            ]
            cats = ["vpn", "printer", "outlook", "mfa", "sap", "password_reset", "docker"]
            for etype, count in spread:
                for k in range(count):
                    ev = AnalyticsEvent(
                        org_id=org.id,
                        event_type=etype,
                        user_id=end_user.id,
                        category=cats[k % len(cats)],
                        properties={"seed": "demo"},
                    )
                    ev.occurred_at = NOW - timedelta(hours=k)
                    s.add(ev)
                    added_events += 1
            await s.flush()

        # ---- AI chat history ----
        have_ai = int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(Conversation)
                    .where(Conversation.org_id == org.id, Conversation.title.like("AI:%"))
                )
            ).scalar_one()
        )
        if have_ai == 0:
            conv = Conversation(
                org_id=org.id,
                user_id=end_user.id,
                status=ConversationStatus.RESOLVED,
                category="password_reset",
                title="AI: Reset my password",
                last_message_at=NOW,
            )
            s.add(conv)
            await s.flush()
            s.add(
                Message(
                    conversation_id=conv.id,
                    turn_id=1,
                    role=MessageRole.USER,
                    content="How do I reset my expired password?",
                    trace_id="seed-ai-1",
                )
            )
            s.add(
                Message(
                    conversation_id=conv.id,
                    turn_id=1,
                    role=MessageRole.ASSISTANT,
                    content="### Issue Detected\nYour domain password has expired.\n\n"
                    "### Recommended Steps\n- [ ] At the Windows logon prompt choose to change "
                    "your password [1]\n- [ ] Pick a password with 12+ characters [1]\n\n"
                    "### Need more help?\n> **Success:** You can sign in with the new password.",
                    decision=Decision.DELIVER,
                    trace_id="seed-ai-1",
                    citations=[
                        {
                            "source_uri": "seed://password_reset/Reset an expired Active Directory password"
                        }
                    ],
                )
            )
            await s.flush()

        await s.commit()

    await engine.dispose()
    print("DEMO SEED COMPLETE")
    print(f"  categories in registry : {len(registry.keys())}")
    print(f"  KB articles added      : {added_kb} (total available: {len(KB_ARTICLES)})")
    print(f"  demo tickets added     : {added_tickets}")
    print(f"  notifications added    : {added_notifs}")
    print(f"  analytics events added : {added_events}")
    print("  users (password ChangeMe123!): admin@/engineer@/sme@/user@acme.com")


if __name__ == "__main__":
    asyncio.run(main())
