# cat > utils.py <<'PY'
# utils.py
from datetime import datetime, date
import calendar
import bcrypt

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

def check_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())

def parse_date(d: str) -> date:
    # expects YYYY-MM-DD
    return datetime.strptime(d, "%Y-%m-%d").date()

def format_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")

def month_end(d: date) -> date:
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last)

def days_in_month_for_date(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]
# PY
