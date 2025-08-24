"""
local_allocations_test.py

Local test harness for "automatic daily allocations" logic using user-controlled splits.

Usage:
  python local_allocations_test.py --create-test
  python local_allocations_test.py --show
  python local_allocations_test.py --run
  python local_allocations_test.py --run --date 2025-08-23
  python local_allocations_test.py --cleanup
"""
import os
import sys
import argparse
from datetime import datetime, date, timedelta
import calendar
from pymongo import MongoClient
from bson.objectid import ObjectId
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    print("Set MONGODB_URI in environment or .env file. Exiting.")
    sys.exit(1)

client = MongoClient(MONGODB_URI)
try:
    db = client.get_default_database()
except Exception:
    db = client["expense_manager"]

users_col = db["users"]
deposits_col = db["deposits"]
transactions_col = db["transactions"]

def to_ymd(d: date) -> str:
    return d.strftime("%Y-%m-%d")

def parse_ymd(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()

def days_in_month_for_date(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]

def month_end_for_date(d: date) -> date:
    last = days_in_month_for_date(d)
    return date(d.year, d.month, last)

def create_test_user(email="test.user@example.com", splits=None):
    if users_col.find_one({"email": email}):
        print("Test user already exists:", email)
        return users_col.find_one({"email": email})
    if splits is None:
        splits = {
            "a_expense": 0.25,
            "b_expense": 0.25,
            "a_savings": 0.25,
            "b_savings": 0.25
        }
    doc = {
        "email": email,
        "password_hash": "test",
        "balances": {
            "a_expense": 0.0,
            "b_expense": 0.0,
            "a_savings": 0.0,
            "b_savings": 0.0
        },
        "last_allocated_date": "1970-01-01",
        "splits": splits
    }
    res = users_col.insert_one(doc)
    user = users_col.find_one({"_id": res.inserted_id})
    print("Created test user:", user["_id"], user["email"], "splits:", user["splits"])
    return user

def create_deposit_for_user(user_id, amount, date_received: date):
    dim = days_in_month_for_date(date_received)
    per_day_total = float(amount) / dim
    doc = {
        "user_id": ObjectId(user_id),
        "amount": float(amount),
        "date_received": to_ymd(date_received),
        "days_in_month": dim,
        "per_day_total": float(per_day_total)
    }
    res = deposits_col.insert_one(doc)
    print("Created deposit:", res.inserted_id, "amount:", amount, "date:", to_ymd(date_received),
          "per_day_total:", round(per_day_total,2))
    return deposits_col.find_one({"_id": res.inserted_id})

def show_test_user(email="test.user@example.com"):
    u = users_col.find_one({"email": email})
    if not u:
        print("No test user found. Run --create-test first.")
        return
    print("USER:", u["_id"], u["email"])
    print("  last_allocated_date:", u.get("last_allocated_date"))
    print("  splits:", u.get("splits"))
    b = u.get("balances", {})
    print("  balances:")
    for k in ["a_expense","b_expense","a_savings","b_savings"]:
        print(f"    {k}: ₹{b.get(k,0.0):.2f}")
    dps = list(deposits_col.find({"user_id": u["_id"]}))
    print("  deposits:")
    for dp in dps:
        print("   -", dp["_id"], dp["date_received"], "₹"+str(dp["amount"]), "per-day:", round(dp["per_day_total"],2))
    txs = list(transactions_col.find({"user_id": u["_id"]}).sort("_id",-1).limit(10))
    print("  recent transactions (last 10):")
    for t in txs:
        print("   -", t.get("date"), t.get("category"), "₹"+str(t.get("amount")), t.get("note",""))

def cleanup_test_data(email_prefix="test."):
    res_users = users_col.delete_many({"email": {"$regex": f"^{email_prefix}"}})
    print("Deleted users:", res_users.deleted_count)
    # heuristic cleanup for deposits and transactions
    res_dp = deposits_col.delete_many({"date_received": {"$regex": r"^202[0-9]-"}})
    res_tx = transactions_col.delete_many({"note": {"$regex": r"^test-"}})
    print("Removed deposits (heuristic):", res_dp.deleted_count)
    print("Removed transactions (heuristic):", res_tx.deleted_count)

def apply_allocations_for_all(today: date = None, verbose=True):
    if today is None:
        today = date.today()
    today_ymd = to_ymd(today)
    if verbose:
        print("Running allocation job for date:", today_ymd)

    cursor = users_col.find({"last_allocated_date": {"$lt": today_ymd}})
    processed = 0
    while True:
        try:
            user = cursor.next()
        except StopIteration:
            break
        except Exception:
            break
        user_id = user["_id"]
        last_allocated_str = user.get("last_allocated_date", "1970-01-01")
        last_allocated_date = parse_ymd(last_allocated_str)
        if verbose:
            print("Processing user:", user["_id"], user["email"], "last_allocated:", last_allocated_str)

        splits = user.get("splits", {
            "a_expense": 0.25, "b_expense": 0.25, "a_savings": 0.25, "b_savings": 0.25
        })

        user_deposits = list(deposits_col.find({"user_id": user_id}))
        inc = {"a_expense": 0.0, "b_expense": 0.0, "a_savings": 0.0, "b_savings": 0.0}

        for dp in user_deposits:
            dep_date = parse_ymd(dp["date_received"])
            dep_month_end = month_end_for_date(dep_date)
            start_apply = max(dep_date, last_allocated_date + timedelta(days=1))
            end_apply = min(dep_month_end, today)
            if start_apply > end_apply:
                continue
            n_days = (end_apply - start_apply).days + 1
            per_day_total = float(dp.get("per_day_total", 0.0))
            if per_day_total <= 0:
                continue
            add_a = per_day_total * splits.get("a_expense", 0.0) * n_days
            add_b = per_day_total * splits.get("b_expense", 0.0) * n_days
            add_as = per_day_total * splits.get("a_savings", 0.0) * n_days
            add_bs = per_day_total * splits.get("b_savings", 0.0) * n_days
            inc["a_expense"] += add_a
            inc["b_expense"] += add_b
            inc["a_savings"] += add_as
            inc["b_savings"] += add_bs
            if verbose:
                print(f"  deposit {dp['_id']} - apply days {to_ymd(start_apply)}..{to_ymd(end_apply)} ({n_days} days)")
                print(f"    per-day total ₹{per_day_total:.2f} -> +₹{round(add_a,2)} a_exp, +₹{round(add_b,2)} b_exp, +₹{round(add_as,2)} a_sav, +₹{round(add_bs,2)} b_sav")

        update = {}
        if any(round(v,2) != 0.0 for v in inc.values()):
            update["$inc"] = {
                "balances.a_expense": round(inc["a_expense"], 2),
                "balances.b_expense": round(inc["b_expense"], 2),
                "balances.a_savings": round(inc["a_savings"], 2),
                "balances.b_savings": round(inc["b_savings"], 2),
            }
        update.setdefault("$set", {})["last_allocated_date"] = today_ymd

        result = users_col.update_one({"_id": user_id, "last_allocated_date": last_allocated_str}, update)
        if result.matched_count == 1:
            processed += 1
            if verbose:
                print("  updated user; inc:", update.get("$inc", {}))
        else:
            if verbose:
                print("  SKIPPED update because last_allocated_date changed concurrently.")

    if verbose:
        print("Allocation job finished. Users processed:", processed)
    return processed

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--create-test", action="store_true")
    parser.add_argument("--email", type=str, default="test.user@example.com")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    if args.create_test:
        # create user with custom splits (change here if you want different ratios)
        example_splits = {
            "a_expense": 0.4,   # 40%
            "b_expense": 0.2,   # 20%
            "a_savings": 0.3,   # 30%
            "b_savings": 0.1    # 10%
        }
        u = create_test_user(email=args.email, splits=example_splits)
        today = date.today()
        dep_day = 11
        try:
            dep_date = date(today.year, today.month, dep_day)
        except ValueError:
            dep_date = date(today.year, today.month, 1)
        if dep_date > today:
            dep_date = date(today.year, today.month, 1)
        create_deposit_for_user(u["_id"], amount=4000, date_received=dep_date)
        print("Test data created. Use --show to inspect.")
        return

    if args.show:
        show_test_user(email=args.email)
        return

    if args.run:
        run_date = None
        if args.date:
            run_date = parse_ymd(args.date)
        apply_allocations_for_all(today=run_date)
        print("Run complete. Use --show to inspect user.")
        return

    if args.cleanup:
        cleanup_test_data()
        print("Cleanup attempted.")
        return

    parser.print_help()

if __name__ == "__main__":
    main()
