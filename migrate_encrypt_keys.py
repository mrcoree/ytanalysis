"""기존 평문 API 키를 암호화로 마이그레이션하는 스크립트"""
import os
import sys

# .env 로드
from dotenv import load_dotenv
load_dotenv()

from app.db.database import SessionLocal
from app.db.models import User
from app.crypto import encrypt, _PREFIX

db = SessionLocal()
try:
    users = db.query(User).filter(
        User.youtube_api_key != "",
        User.youtube_api_key.isnot(None),
    ).all()

    migrated = 0
    for user in users:
        key = user.youtube_api_key
        if key and not key.startswith(_PREFIX):
            user.youtube_api_key = encrypt(key)
            migrated += 1
            print(f"  Encrypted key for user: {user.username}")

    if migrated:
        db.commit()
        print(f"\nDone: {migrated} key(s) encrypted.")
    else:
        print("No plaintext keys found — nothing to migrate.")
finally:
    db.close()
