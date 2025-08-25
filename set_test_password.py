# set_test_password.py
from pymongo import MongoClient
from dotenv import load_dotenv
import os
import bcrypt

load_dotenv()
MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise RuntimeError("Set MONGODB_URI in .env")

client = MongoClient(MONGODB_URI)
try:
    db = client.get_default_database()
except Exception:
    db = client["expense_manager"]

users = db["users"]

email = "test.user@example.com"
new_pw = "password123"

pw_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
res = users.update_one({"email": email}, {"$set": {"password_hash": pw_hash}})
print("matched:", res.matched_count, "modified:", res.modified_count)
print("You can now log in at http://localhost:8501 with:")
print("  email:", email)
print("  password:", new_pw)
