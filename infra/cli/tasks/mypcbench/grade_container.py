#!/usr/bin/env python3
"""Host-side deterministic grader for MyPCBench action tasks.

The runner invokes this as `uv run python grade_container.py` with a JSON context
on stdin (task_id, ...). It checks the real app state in the apps container
(`mypc-apps`) via `docker exec ... sqlite3` and prints ONE JSON line:
  {"score", "max_score", "checkpoints":[...], "log"}

Each task is worth 100; the check is a single COUNT query against the seeded
per-VM DB at /data/vms/<VM_ID>/<app>.sqlite, with an "absolute post-condition"
predicate (a new row carrying a unique marker, or a flag/status flipped on a
specific existing row). This is the deterministic analog of MyPCBench's offline
LLM judge — we read ground truth instead of judging prose.

To add a task: append one entry to SPECS keyed by the task's `id`. Markers were
chosen to be absent from the pristine seed (create/compose) or to target a
specific seeded row (flag/status), so graders are baseline-free and re-run-safe.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Callable

CONTAINER = "mypc-apps"
VM_ID = "michael_scott"
WORLD = "scranton-office"

# Overrides for world-scoped apps; the rest live at /data/vms/<VM_ID>/<app>.sqlite.
DB_PATHS = {
    "lockedin": f"/data/worlds/{WORLD}/lockedin.sqlite",
}

# task_id -> (app, count-query, predicate(n)->bool, human label)
SPECS: dict[str, tuple[str, str, Callable[[int], bool], str]] = {
    # --- calendar: create event with a unique title ---
    "mypc-calendar-improv": (
        "hoolicalendar", "SELECT count(*) FROM events WHERE title LIKE '%Improv Night%';",
        lambda n: n >= 1, "events titled 'Improv Night'"),
    "mypc-calendar-dentist": (
        "hoolicalendar", "SELECT count(*) FROM events WHERE title LIKE '%Dentist Checkup%';",
        lambda n: n >= 1, "events titled 'Dentist Checkup'"),
    "mypc-calendar-teamsync": (
        "hoolicalendar", "SELECT count(*) FROM events WHERE title LIKE '%Team Sync%';",
        lambda n: n >= 1, "events titled 'Team Sync'"),
    "mypc-calendar-pto": (
        "hoolicalendar", "SELECT count(*) FROM events WHERE title LIKE '%PTO Day%';",
        lambda n: n >= 1, "events titled 'PTO Day'"),
    # --- mail: flag a specific email / send a new one ---
    "mypc-mail-star-tax": (
        "mail", "SELECT count(*) FROM mail_entry_state s JOIN emails e ON e.id=s.email_id "
                "WHERE s.starred=1 AND e.subject LIKE '%NY State Tax%';",
        lambda n: n >= 1, "starred NY-State-Tax emails"),
    "mypc-mail-read-eticket": (
        "mail", "SELECT count(*) FROM mail_entry_state s JOIN emails e ON e.id=s.email_id "
                "WHERE s.read=1 AND e.subject LIKE '%E-Ticket Receipt: Dinoco%';",
        lambda n: n >= 1, "read Dinoco e-ticket emails"),
    "mypc-mail-important-spectrum": (
        "mail", "SELECT count(*) FROM mail_entry_state s JOIN emails e ON e.id=s.email_id "
                "WHERE s.important=1 AND e.subject LIKE '%Spectrum Internet%';",
        lambda n: n >= 1, "important-flagged Spectrum emails"),
    "mypc-mail-compose-pam": (
        "mail", "SELECT count(*) FROM emails WHERE subject LIKE '%Lunch Friday%' "
                "AND to_email LIKE '%pam%';",
        lambda n: n >= 1, "sent 'Lunch Friday?' emails to Pam"),
    # --- shop: add to cart (isolated app; count delta over seed baseline of 4) ---
    "mypc-shop-cart": (
        "hoolishop", "SELECT count(*) FROM cart_items;",
        lambda n: n > 4, "cart_items (seed baseline 4)"),
    # --- sprintboard: create a task / move a task to done ---
    "mypc-sprint-create": (
        "sprintboard", "SELECT count(*) FROM tasks WHERE title LIKE '%Restock the breakroom%';",
        lambda n: n >= 1, "tasks titled 'Restock the breakroom'"),
    "mypc-sprint-trophies-done": (
        "sprintboard", "SELECT count(*) FROM tasks WHERE title LIKE 'Order custom trophies%' "
                       "AND status='done';",
        lambda n: n >= 1, "'Order custom trophies' tasks in done"),
    # --- vaultbank: send a Zelle / schedule a bill (distinctive amount marker) ---
    "mypc-bank-zelle-pam": (
        "vaultbank", "SELECT count(*) FROM zelle_transfers WHERE direction='sent' "
                     "AND contact_name LIKE 'Pam%' AND amount=12.34;",
        lambda n: n >= 1, "Zelle $12.34 sent to Pam"),
    "mypc-bank-bill-netflix": (
        "vaultbank", "SELECT count(*) FROM bill_payments WHERE payee LIKE 'Netflix%' "
                     "AND amount=9.99;",
        lambda n: n >= 1, "$9.99 Netflix bill payments"),
    # --- batbucks: place a buy order (distinctive share count marker) ---
    "mypc-batbucks-buy-aapl": (
        "batbucks", "SELECT count(*) FROM orders WHERE ticker='AAPL' AND side='buy' "
                    "AND shares=7;",
        lambda n: n >= 1, "AAPL buy orders for 7 shares"),
    # ===== batch 2: +36 single-app GUI-bound action tasks (probe-validated) =====
    "mypc-gringotts-zelle-send": (
        "vaultbank", "SELECT count(*) FROM zelle_transfers WHERE direction='sent' AND amount=246.81 AND contact_name='Wendy Calloway';",
        lambda n: n >= 1, "Zelle $246.81 sent to new contact Wendy Calloway"),
    "mypc-gringotts-add-biller": (
        "vaultbank", "SELECT count(*) FROM vaultbank_billers WHERE name='Aurora Solar Cooperative';",
        lambda n: n >= 1, "Billers named 'Aurora Solar Cooperative'"),
    "mypc-gringotts-pay-bill": (
        "vaultbank", "SELECT count(*) FROM bill_payments WHERE amount=158.73 AND memo LIKE '%Summer AC surcharge%';",
        lambda n: n >= 1, "Bill payments of $158.73 with memo 'Summer AC surcharge'"),
    "mypc-batbucks-limit-buy": (
        "batbucks", "SELECT count(*) FROM orders WHERE ticker='AAPL' AND side='buy' AND order_type='limit' AND limit_price=188.88;",
        lambda n: n >= 1, "AAPL limit buy orders at $188.88"),
    "mypc-batbucks-price-alert": (
        "batbucks", "SELECT count(*) FROM price_alerts WHERE ticker='TSLA' AND direction='below' AND target_price=312.67;",
        lambda n: n >= 1, "TSLA 'below $312.67' price alerts"),
    "mypc-odds-limit-buy-yes": (
        "oddsmarket", "SELECT count(*) FROM trade_history WHERE market_ticker='CRYPTO-BTC-150K-2026' AND action='buy' AND side='YES' AND shares=17;",
        lambda n: n >= 1, "Buy-YES trades of 17 shares on the BTC-$150K market"),
    "mypc-odds-market-buy-no": (
        "oddsmarket", "SELECT count(*) FROM trade_history WHERE market_ticker='TECH-TIKTOK-BAN-2026' AND action='buy' AND side='NO' AND shares=29;",
        lambda n: n >= 1, "Buy-NO market trades of 29 shares on the TikTok-ban market"),
    "mypc-hangrydash-lobster-roll-cart": (
        "hangrydash", "SELECT count(*) FROM cart_items WHERE restaurant_id=1 AND menu_item_id=451 AND quantity=4;",
        lambda n: n >= 1, "Add 4 Lobster Rolls (Cooper's Seafood House) to cart"),
    "mypc-hangrydash-dumpling-checkout": (
        "hangrydash", "SELECT count(*) FROM orders WHERE restaurant_id=11 AND items LIKE '%\"menu_item_id\":232%' AND items LIKE '%\"qty\":2%';",
        lambda n: n >= 1, "Checkout 2x Crab Soup Dumplings from Dumpling Home"),
    "mypc-hangrydash-coopers-review": (
        "hangrydash", "SELECT count(*) FROM order_reviews WHERE restaurant_id=1 AND rating=5 AND review_text LIKE '%Best baby back ribs in all of Scranton%';",
        lambda n: n >= 1, "Write a 5-star Cooper's Seafood House order review"),
    "mypc-hoolishop-kettle-subscribe": (
        "hoolishop", "SELECT count(*) FROM subscribe_save WHERE product_id=16 AND frequency='monthly' AND active=1;",
        lambda n: n >= 1, "Subscribe & Save the Electric Kettle 1.7L (monthly)"),
    "mypc-hoolishop-skillet-order-office": (
        "hoolishop", "SELECT count(*) FROM orders WHERE items_json LIKE '%\"product_id\":8,%' AND items_json LIKE '%\"quantity\":2%' AND shipping_address LIKE '%Suite 200%';",
        lambda n: n >= 1, "Checkout 2x Cast Iron Skillet to the Office address"),
    "mypc-kwik-bbq-shopping-list": (
        "kwik-e-mart", "SELECT count(*) FROM shopping_list_items sli JOIN shopping_lists sl ON sl.id=sli.list_id WHERE sl.name='Memorial Day BBQ Run';",
        lambda n: n >= 3, "'Memorial Day BBQ Run' shopping list with 3 items"),
    "mypc-kwik-pricechopper-cart": (
        "kwik-e-mart", "SELECT count(*) FROM cart_items WHERE store_id=2 AND product_id IN (45,50);",
        lambda n: n >= 2, "Kombucha x6 and French Green Lentils x2 (Price Chopper) in cart"),
    "mypc-tablefind-book-coopers": (
        "tablefind", "SELECT count(*) FROM reservations WHERE restaurant_id=1 AND date='2026-07-15' AND time='19:00' AND party_size=2 AND status='confirmed';",
        lambda n: n >= 1, "Book a 2-top at Cooper's Seafood House for Jul 15 2026 7:00 PM"),
    "mypc-tablefind-waitlist-alfredos": (
        "tablefind", "SELECT count(*) FROM waitlist_entries WHERE restaurant_id=6 AND date='2026-07-20' AND preferred_time='20:00' AND party_size=4 AND status='active';",
        lambda n: n >= 1, "Join Alfredo's Pizza Cafe waitlist, party of 4, Jul 20 2026 8:00 PM"),
    "mypc-cheskepdia-book-rittenhouse": (
        "cheskepdia", "SELECT count(*) FROM bookings WHERE property_name='Classic 1BR in Rittenhouse Square' AND check_in='2026-08-10' AND check_out='2026-08-14' AND guests=2 AND status='confirmed';",
        lambda n: n >= 1, "Book Rittenhouse Square 1BR, 2 guests, Aug 10-14 2026"),
    "mypc-cheskepdia-save-greenwich": (
        "cheskepdia", "SELECT count(*) FROM saved_properties WHERE property_name='The Greenwich Hotel';",
        lambda n: n >= 1, "Save 'The Greenwich Hotel' to saved properties"),
    "mypc-etaxi-request-lake-overlook": (
        "etaxi", "SELECT count(*) FROM ride_requests WHERE dropoff='Lake Scranton Overlook';",
        lambda n: n >= 1, "Request an eTaxi ride to Lake Scranton Overlook"),
    "mypc-etaxi-rate-alfredos-ride": (
        "etaxi", "SELECT count(*) FROM ride_ratings WHERE ride_id=84;",
        lambda n: n >= 1, "Rate the Jan 7 2026 Alfredo's Pizza Cafe ride 5 stars"),
    "mypc-dinoco-checkin-dn0991": (
        "dinoco-airlines", "SELECT count(*) FROM flights WHERE flight_number='DN0991' AND checked_in=1;",
        lambda n: n >= 1, "Check in for flight DN0991 (AVP to PHL)"),
    "mypc-dinoco-seat-dn1563": (
        "dinoco-airlines", "SELECT count(*) FROM flights WHERE flight_number='DN1563' AND seat='16A';",
        lambda n: n >= 1, "Change seat on flight DN1563 to 16A"),
    "mypc-hoolichat-dm-pam-onboarding": (
        "buzzchat", "SELECT count(*) FROM messages WHERE conversation_id='conv-0006' AND sender_email='michael.scott@dundermifflin.com' AND content LIKE '%Penny Marlin onboarding packet#PMX7%';",
        lambda n: n >= 1, "Send a 1:1 DM to Pam Beesly with marker #PMX7"),
    "mypc-hoolichat-react-jim-message": (
        "buzzchat", "SELECT count(*) FROM message_reactions WHERE message_id='msg-0125' AND user_email='michael.scott@dundermifflin.com';",
        lambda n: n >= 1, "React to Jim Halpert's 'can you talk?' message"),
    "mypc-hooliwork-channel-sales-audit": (
        "workbuzz", "SELECT count(*) FROM messages WHERE channel_id=2 AND sender_email='michael.scott@dundermifflin.com' AND content LIKE '%Q3 paper supplier audit#WB42%';",
        lambda n: n >= 1, "Post a #sales channel message with marker #WB42"),
    "mypc-hooliwork-create-channel-dundies": (
        "workbuzz", "SELECT count(*) FROM channels WHERE name='dundie-awards-2026' AND created_by='michael.scott@dundermifflin.com';",
        lambda n: n >= 1, "Create channel 'dundie-awards-2026'"),
    "mypc-hooliwork-save-wallace-message": (
        "workbuzz", "SELECT count(*) FROM saved_items WHERE user_email='michael.scott@dundermifflin.com' AND target_type='message' AND target_id=4;",
        lambda n: n >= 1, "Save/bookmark David Wallace's quarterly-review message"),
    "mypc-lockedin-accept-erin-hannon": (
        "lockedin", "SELECT count(*) FROM connection_requests WHERE from_email='erin.hannon@dundermifflin.com' AND to_email='michael.scott@dundermifflin.com' AND status='accepted';",
        lambda n: n >= 1, "Accept the pending connection request from Erin Hannon"),
    "mypc-lockedin-apply-prodops-job": (
        "lockedin", "SELECT count(*) FROM applications WHERE job_id=1 AND user_email='michael.scott@dundermifflin.com';",
        lambda n: n >= 1, "Apply to the Senior Product Operations Manager job"),
    "mypc-lockedin-post-q2-results": (
        "lockedin", "SELECT count(*) FROM posts WHERE user_email='michael.scott@dundermifflin.com' AND content LIKE '%Dunder Mifflin Scranton crushed Q2 paper targets#LP55%';",
        lambda n: n >= 1, "Publish a LockedIn feed post with marker #LP55"),
    "mypc-sprintboard-comment-dundies": (
        "sprintboard", "SELECT count(*) FROM comments c JOIN tasks t ON t.id=c.task_id WHERE t.task_key='TMIQ-1' AND c.body LIKE '%DUNDIE-ACK-7731%';",
        lambda n: n >= 1, "Comment 'DUNDIE-ACK-7731' on task TMIQ-1"),
    "mypc-sprintboard-priority-urgent-playlist": (
        "sprintboard", "SELECT count(*) FROM tasks WHERE task_key='TMIQ-6' AND priority='urgent';",
        lambda n: n >= 1, "Set task TMIQ-6 priority to Urgent"),
    "mypc-speedtax-file-2025-return": (
        "speedtax", "SELECT count(*) FROM tax_returns WHERE tax_year=2025 AND status='filed';",
        lambda n: n >= 1, "File the in-progress 2025 tax return"),
    "mypc-hoolicalendar-add-attendee-improv": (
        "hoolicalendar", "SELECT count(*) FROM event_attendees a JOIN events e ON a.event_id=e.id WHERE a.email='casting.scout.dundie7731@improv.test' AND e.title LIKE '%UCB Improv Workshop%';",
        lambda n: n >= 1, "Add an attendee to the UCB Improv Workshop event"),
    "mypc-hoolicalendar-add-reminder-philly": (
        "hoolicalendar", "SELECT count(*) FROM event_reminders r JOIN events e ON e.id=r.event_id WHERE r.minutes_before=42 AND e.title LIKE '%Philadelphia, PA - weekend road trip%';",
        lambda n: n >= 1, "Add a 42-minute reminder to the Philadelphia road trip event"),
    "mypc-hoolimail-move-sandals-travel": (
        "mail", "SELECT count(*) FROM mail_entry_state s JOIN emails e ON e.id=s.email_id AND e.user_email=s.user_email WHERE e.subject LIKE 'Your Sandals Montego Bay reservation is confirmed (SM-88431)%' AND s.folder='travel';",
        lambda n: n >= 1, "Move the Sandals Montego Bay confirmation email to Travel"),
}


def count(app: str, query: str) -> int:
    db = DB_PATHS.get(app, f"/data/vms/{VM_ID}/{app}.sqlite")
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "sqlite3", db, query],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return int((out.stdout or "").strip() or "0")
    except ValueError:
        return 0


def main() -> None:
    try:
        ctx = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        ctx = {}
    task_id = ctx.get("task_id", "")
    try:
        spec = SPECS.get(task_id)
        if spec is None:
            raise KeyError(f"no grader registered for {task_id!r}")
        app, query, predicate, label = spec
        n = count(app, query)
        passed = predicate(n)
        detail = f"{label} = {n}"
        print(json.dumps({
            "score": 100.0 if passed else 0.0,
            "max_score": 100.0,
            "checkpoints": [{"name": task_id, "passed": passed, "detail": detail, "weight": 100}],
            "log": f"{task_id}: {'PASS' if passed else 'FAIL'} — {detail}",
        }))
    except Exception as e:  # noqa: BLE001 — never crash the runner's grade fold
        print(json.dumps({
            "score": 0.0, "max_score": 100.0, "checkpoints": [],
            "log": f"{task_id}: grader error {type(e).__name__}: {e}",
        }))


if __name__ == "__main__":
    main()
