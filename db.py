# # db.py
# from pymongo import MongoClient
# from bson.objectid import ObjectId
# import os
# from dotenv import load_dotenv
# load_dotenv()

# MONGODB_URI = os.getenv("MONGODB_URI")
# if not MONGODB_URI:
#     raise RuntimeError("Set MONGODB_URI in .env")

# client = MongoClient(MONGODB_URI)
# db = client.get_default_database()  # uses DB from connection string or fallback

# users = db["users"]
# deposits = db["deposits"]
# transactions = db["transactions"]
# bills = db["bills"]

# def get_user_by_email(email):
#     return users.find_one({"email": email})

# def create_user(email, password_hash, splits=None):
#     if splits is None:
#         splits = {
#             "a_expense": 0.25,
#             "b_expense": 0.25,
#             "a_savings": 0.25,
#             "b_savings": 0.25
#         }
#     doc = {
#         "email": email,
#         "password_hash": password_hash,
#         "balances": {
#             "a_expense": 0.0,
#             "b_expense": 0.0,
#             "a_savings": 0.0,
#             "b_savings": 0.0
#         },
#         "last_allocated_date": "1970-01-01",
#         "splits": splits
#     }
#     res = users.insert_one(doc)
#     return users.find_one({"_id": res.inserted_id})

# def get_user_by_id(uid):
#     return users.find_one({"_id": ObjectId(uid)})

# def update_user_balances_and_date(user_id, balances, last_allocated_date):
#     users.update_one({"_id": ObjectId(user_id)}, {"$set": {"balances": balances, "last_allocated_date": last_allocated_date}})

# def update_user_splits(user_id, splits):
#     users.update_one({"_id": ObjectId(user_id)}, {"$set": {"splits": splits}})

# def insert_deposit(user_id, original_amount, date_received, days_in_month, per_day_total, bills_total=0.0, bills_applied=None):
#     """
#     Store deposit with original_amount and per_day_total (after deducting monthly bills).
#     bills_applied: optional list of {name, amount} objects for transparency.
#     """
#     doc = {
#         "user_id": ObjectId(user_id),
#         "original_amount": float(original_amount),
#         "amount": float(original_amount) - float(bills_total),  # net after bills
#         "date_received": date_received,  # YYYY-MM-DD
#         "days_in_month": int(days_in_month),
#         "per_day_total": float(per_day_total),
#         "bills_total": float(bills_total),
#         "bills_applied": bills_applied or []
#     }
#     return deposits.insert_one(doc)

# def list_deposits_for_user(user_id):
#     return list(deposits.find({"user_id": ObjectId(user_id)}).sort("date_received", 1))

# def add_transaction(user_id, date, category, amount, note=""):
#     # amount is stored as positive; transactions represent "money moved out" for spends/bills
#     doc = {
#         "user_id": ObjectId(user_id),
#         "date": date,
#         "category": category,
#         "amount": float(amount),
#         "note": note
#     }
#     return transactions.insert_one(doc)

# def list_transactions_for_user(user_id, limit=100):
#     return list(transactions.find({"user_id": ObjectId(user_id)}).sort("_id", -1).limit(limit))

# def count_users():
#     return users.count_documents({})

# # ---- Bills helpers ----
# def add_bill(user_id, name, monthly_amount):
#     doc = {
#         "user_id": ObjectId(user_id),
#         "name": name,
#         "monthly_amount": float(monthly_amount)
#     }
#     return bills.insert_one(doc)

# def list_bills_for_user(user_id):
#     return list(bills.find({"user_id": ObjectId(user_id)}).sort("name", 1))

# def delete_bill(user_id, bill_id):
#     return bills.delete_one({"_id": ObjectId(bill_id), "user_id": ObjectId(user_id)})

# def total_bills_for_user_month(user_id, year, month):
#     """
#     Returns total monthly bills amount (sum of monthly_amount) for the given user.
#     For now, bills are considered fixed monthly amounts (no per-bill due date).
#     """
#     b = list_bills_for_user(user_id)
#     total = 0.0
#     for item in b:
#         total += float(item.get("monthly_amount", 0.0))
#     return total, b


# db.py
from pymongo import MongoClient
from bson.objectid import ObjectId
import os
from dotenv import load_dotenv
load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise RuntimeError("Set MONGODB_URI in .env")

client = MongoClient(MONGODB_URI)
db = client.get_default_database()  # uses DB from connection string or fallback

users = db["users"]
deposits = db["deposits"]
transactions = db["transactions"]
bills = db["bills"]

def get_user_by_email(email):
    return users.find_one({"email": email})

def create_user(email, password_hash):
    doc = {
        "email": email,
        "password_hash": password_hash,
        "balances": {
            "a_expense": 0.0,
            "b_expense": 0.0,
            "a_savings": 0.0,
            "b_savings": 0.0
        },
        "last_allocated_date": "1970-01-01",
        "splits": {  # Fixed equal splits
            "a_expense": 0.25,
            "b_expense": 0.25,
            "a_savings": 0.25,
            "b_savings": 0.25
        }
    }
    res = users.insert_one(doc)
    return users.find_one({"_id": res.inserted_id})

def get_user_by_id(uid):
    return users.find_one({"_id": ObjectId(uid)})

def update_user_balances_and_date(user_id, balances, last_allocated_date):
    users.update_one({"_id": ObjectId(user_id)}, {"$set": {"balances": balances, "last_allocated_date": last_allocated_date}})

def insert_deposit(user_id, original_amount, date_received, days_in_month, per_day_total, bills_total=0.0, bills_applied=None):
    doc = {
        "user_id": ObjectId(user_id),
        "original_amount": float(original_amount),
        "amount": float(original_amount) - float(bills_total),  # net after bills
        "date_received": date_received,  # YYYY-MM-DD
        "days_in_month": int(days_in_month),
        "per_day_total": float(per_day_total),
        "bills_total": float(bills_total),
        "bills_applied": bills_applied or []
    }
    return deposits.insert_one(doc)

def list_deposits_for_user(user_id):
    return list(deposits.find({"user_id": ObjectId(user_id)}).sort("date_received", 1))

def add_transaction(user_id, date, category, amount, note=""):
    doc = {
        "user_id": ObjectId(user_id),
        "date": date,
        "category": category,
        "amount": float(amount),
        "note": note
    }
    return transactions.insert_one(doc)

def list_transactions_for_user(user_id, limit=100):
    return list(transactions.find({"user_id": ObjectId(user_id)}).sort("_id", -1).limit(limit))

def count_users():
    return users.count_documents({})

# ---- Bills helpers ----
def add_bill(user_id, name, monthly_amount):
    doc = {
        "user_id": ObjectId(user_id),
        "name": name,
        "monthly_amount": float(monthly_amount)
    }
    return bills.insert_one(doc)

def list_bills_for_user(user_id):
    return list(bills.find({"user_id": ObjectId(user_id)}).sort("name", 1))

def delete_bill(user_id, bill_id):
    return bills.delete_one({"_id": ObjectId(bill_id), "user_id": ObjectId(user_id)})

def total_bills_for_user_month(user_id, year, month):
    b = list_bills_for_user(user_id)
    total = 0.0
    for item in b:
        total += float(item.get("monthly_amount", 0.0))
    return total, b