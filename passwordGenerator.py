# passwordGenerator.py
# Robust generator for a bcrypt hash compatible with streamlit-authenticator.
# Usage: python passwordGenerator.py
# It will print a list of hashes and a small YAML snippet to paste into auth_config.yaml,
# and perform a verification check to demonstrate the plaintext verifies against the hash.

import sys
import pprint

passwords = ["qwertyuiop"]   # change to the plaintext(s) you want to hash

hashed = None
method_used = None

# Try new API: streamlit_authenticator.utilities.hasher.Hasher.hash_list
try:
    from streamlit_authenticator.utilities.hasher import Hasher as SAHasher
    try:
        hashed = SAHasher.hash_list(passwords)
        method_used = "streamlit_authenticator.utilities.hasher.Hasher.hash_list"
    except Exception as e:
        hashed = None
except Exception:
    hashed = None

# Try older API: streamlit_authenticator.Hasher(passwords).generate()
if hashed is None:
    try:
        import streamlit_authenticator as stauth
        try:
            # older examples used stauth.Hasher(passwords).generate()
            hashed = stauth.Hasher(passwords).generate()
            method_used = "streamlit_authenticator.Hasher(...).generate()"
        except TypeError:
            # some versions expect a different call signature; ignore
            hashed = None
        except Exception:
            hashed = None
    except Exception:
        hashed = None

# Fallback: use bcrypt directly
if hashed is None:
    try:
        import bcrypt
    except Exception as e:
        print("ERROR: bcrypt not available. Install it with: pip install bcrypt")
        raise

    hashed = []
    for p in passwords:
        h = bcrypt.hashpw(p.encode("utf-8"), bcrypt.gensalt())  # gensalt default rounds (12)
        hashed.append(h.decode("utf-8"))
    method_used = "bcrypt.hashpw(..., bcrypt.gensalt()) (fallback)"

# Print results
print("Method used to generate hash:", method_used)
print("\nGenerated hash(es):")
pprint.pprint(hashed)

# Show YAML snippet you can paste (single-user example)
username = "bob"   # change to the username you'll use in auth_config.yaml
yml = f"""
# Paste into auth_config.yaml (replace cookie section as needed)
credentials:
  usernames:
    {username}:
      name: {username.capitalize()}
      password: "{hashed[0]}"
cookie:
  name: my_cookie_name
  key: some_random_key_here
  expiry_days: 30
"""
print("\nYAML snippet to paste into auth_config.yaml:\n")
print(yml)

# Verification check with bcrypt to demonstrate matching
print("Verification check:")
try:
    import bcrypt
    ok = bcrypt.checkpw(passwords[0].encode("utf-8"), hashed[0].encode("utf-8"))
    print(f"bcrypt.checkpw(plaintext, generated_hash) -> {ok}  (expected: True)")
except Exception as e:
    print("Could not run bcrypt.checkpw verification:", e)

# Helpful notes:
print("\nNotes:")
print("- bcrypt hashes will differ each time you generate them (because of random salt).")
print("- If your auth_config.yaml contains bcrypt hashes, ensure your app calls:")
print("    stauth.Authenticate(..., auto_hash=False)")
print("  so the library does not re-hash those hash strings.")
print("- If your auth_config.yaml contains plaintext passwords, leave auto_hash=True (default).")
