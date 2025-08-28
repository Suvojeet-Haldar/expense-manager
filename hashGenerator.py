#!/usr/bin/env python3
"""
hashGenerator.py

Robust password-hash generator for use with streamlit-authenticator YAML.
Tries passlib -> bcrypt -> streamlit-authenticator (multiple API variations).
Usage:
    python hashGenerator.py password1 password2
Or just:
    python hashGenerator.py
  and you'll be prompted (hidden input).
"""
import sys
import getpass
import traceback

def try_passlib(pw):
    try:
        from passlib.hash import bcrypt as pbcrypt
        return pbcrypt.hash(pw)
    except Exception as e:
        raise

def try_bcrypt_lib(pw):
    try:
        import bcrypt
        h = bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt())
        return h.decode('utf-8')
    except Exception as e:
        raise

def try_stauth_variants(passwords):
    """
    Try multiple streamlit-authenticator Hasher call patterns to work across versions.
    Returns a list of hashes.
    """
    import streamlit_authenticator as stauth
    # Try several likely call patterns
    attempts = []
    patterns = [
        ("Hasher(passwords=list).generate()", lambda pwlist: stauth.Hasher(passwords=pwlist).generate()),
        ("Hasher(list).generate()", lambda pwlist: stauth.Hasher(pwlist).generate()),
        ("Hasher().generate(list)", lambda pwlist: stauth.Hasher().generate(pwlist)),
        ("Hasher(passwords=list).generate_hashes()", lambda pwlist: stauth.Hasher(passwords=pwlist).generate_hashes()),
        ("Hasher(list).generate_hashes()", lambda pwlist: stauth.Hasher(pwlist).generate_hashes()),
        ("Hasher().generate_hashes(list)", lambda pwlist: stauth.Hasher().generate_hashes(pwlist)),
    ]
    for name, fn in patterns:
        try:
            res = fn(passwords)
            # Normalize result to list of strings
            if isinstance(res, list):
                return [str(x) for x in res]
            if isinstance(res, str):
                return [res]
            # If something else, attempt to coerce
            return [str(x) for x in res]
        except Exception as e:
            attempts.append((name, repr(e)))
    # If nothing worked, raise a diagnostic error
    raise RuntimeError("streamlit-authenticator Hasher variants failed. Attempts:\n" +
                       "\n".join(f"{n}: {err}" for n, err in attempts))

def generate_hashes(passwords):
    # try passlib first (recommended)
    try:
        hashes = [try_passlib(pw) for pw in passwords]
        backend = "passlib"
        return backend, hashes
    except Exception:
        pass

    # try bcrypt library
    try:
        hashes = [try_bcrypt_lib(pw) for pw in passwords]
        backend = "bcrypt"
        return backend, hashes
    except Exception:
        pass

    # try streamlit-authenticator variants
    try:
        backend = "streamlit-authenticator"
        hashes = try_stauth_variants(passwords)
        return backend, hashes
    except Exception as st_e:
        # Provide detailed diagnostics
        diag = []
        diag.append("Tried passlib, bcrypt, and streamlit-authenticator variants and none succeeded.")
        diag.append("Exception from stauth attempt:\n" + "".join(traceback.format_exception_only(type(st_e), st_e)))
        raise RuntimeError("\n".join(diag))

def main():
    if len(sys.argv) > 1:
        pwlist = sys.argv[1:]
    else:
        # prompt for one or more passwords
        print("Enter passwords (one per line). Empty line to finish.")
        pwlist = []
        while True:
            try:
                p = getpass.getpass("Password: ")
            except Exception:
                # fallback to normal input if getpass fails
                p = input("Password: ")
            if not p:
                break
            pwlist.append(p)
        if not pwlist:
            print("No passwords provided, exiting.")
            return

    try:
        backend, hashes = generate_hashes(pwlist)
    except Exception as e:
        print("ERROR: Could not generate hashes automatically.")
        print(str(e))
        print("\nRecommended fixes (pick one):")
        print("  1) Install passlib with bcrypt support (recommended):")
        print("       pip install 'passlib[bcrypt]'")
        print("  2) Or install bcrypt directly:")
        print("       pip install bcrypt")
        print("  3) If you prefer streamlit-authenticator hashing, ensure streamlit-authenticator is installed:")
        print("       pip install streamlit-authenticator")
        print("\nAlso you can paste these passwords into a Streamlit UI to generate hashes if you want.")
        return

    print(f"\nHashes generated using backend: {backend}\n")
    for pw, h in zip(pwlist, hashes):
        print(f"password: (hidden) -> hash:\n{h}\n")

    # YAML helper snippet
    print("YAML snippet to copy into auth_config.yaml (fill username/email/name):\n")
    print("credentials:")
    print("  usernames:")
    for i, h in enumerate(hashes):
        user = f"user{i+1}"
        email = f"{user}@example.com"
        name = f"User {i+1}"
        print(f"    {user}:")
        print(f"      email: {email}")
        print(f"      name: {name}")
        print(f"      password: \"{h}\"")
    print("\ncookie:")
    print("  name: expense_manager_auth")
    print("  key: change_this_to_a_random_secret_key")
    print("  expiry_days: 30")
    print("\npreauthorized:")
    print("  emails: []")
    print("\n-- Paste the hashed password string(s) into auth_config.yaml as above. --")

if __name__ == "__main__":
    main()
