# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Authentication services and user store management."""

import os
import sqlite3
import datetime
import logging
from typing import Optional, Dict, Any
import bcrypt
import jwt
from google.cloud.firestore import AsyncClient

logger = logging.getLogger("ask_your_data.auth")

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-key-ask-your-data-12345")
JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Hashes a password using bcrypt.

    Args:
        password: The plain text password.

    Returns:
        The hashed password string.
    """
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verifies a password against a bcrypt hash.

    Args:
        password: The plain text password.
        hashed: The hashed password.

    Returns:
        True if the password matches, False otherwise.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as e:
        logger.error("Error verifying password: %s", e)
        return False


def create_jwt_token(email: str, expires_in_hours: int = 24) -> str:
    """Creates a JWT token for the user.

    Args:
        email: The user's email.
        expires_in_hours: Optional token lifespan in hours.

    Returns:
        The encoded JWT token string.
    """
    payload = {
        "sub": email,
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=expires_in_hours),
        "iat": datetime.datetime.now(datetime.timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_jwt_token(token: str) -> Optional[str]:
    """Verifies a JWT token and returns the subject (email).

    Args:
        token: The JWT token.

    Returns:
        The user's email if valid, None otherwise.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except Exception:
        return None


class UserStore:
    """Abstraction layer for managing user data locally and in production."""

    def __init__(self, is_deployed: bool = False, db_path: str = "local_users.db") -> None:
        """Initializes the UserStore.

        Args:
            is_deployed: If True, uses Firestore. Otherwise, uses SQLite.
            db_path: The SQLite database file path.
        """
        self.is_deployed = is_deployed
        self.db_path = db_path
        self.collection_name = "users"
        self.admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()

        if not self.is_deployed:
            self._init_sqlite()
        else:
            self.firestore_client = AsyncClient()

    def _init_sqlite(self) -> None:
        """Creates the sqlite users table and runs migration upgrades if needed."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Migration check: check if is_admin column exists
            cursor.execute("PRAGMA table_info(users)")
            columns = [col[1] for col in cursor.fetchall()]
            if "is_admin" not in columns:
                cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")

            # Automatically promote default admin to admin status
            cursor.execute("UPDATE users SET is_admin = 1 WHERE email = ?", (self.admin_email,))
            conn.commit()
        finally:
            conn.close()

    async def get_user(self, email: str) -> Optional[Dict[str, Any]]:
        """Retrieves a user by email.

        Args:
            email: The user's email.

        Returns:
            A dictionary containing user details, or None if not found.
        """
        email = email.strip().lower()
        if self.is_deployed:
            doc_ref = self.firestore_client.collection(self.collection_name).document(email)
            doc = await doc_ref.get()
            if doc.exists:
                doc_data = doc.to_dict() or {}
                if email == self.admin_email and not doc_data.get("is_admin"):
                    doc_data["is_admin"] = True
                    await doc_ref.update({"is_admin": True})
                return doc_data
            return None
        else:
            def _get():
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id, email, password_hash, is_admin, created_at FROM users WHERE email = ?", (email,))
                    row = cursor.fetchone()
                    if row:
                        return {
                            "id": row[0],
                            "email": row[1],
                            "password_hash": row[2],
                            "is_admin": bool(row[3]),
                            "created_at": row[4]
                        }
                    return None
                finally:
                    conn.close()
            import asyncio
            return await asyncio.to_thread(_get)

    async def create_user(self, email: str, password_hash: str) -> Dict[str, Any]:
        """Creates a new user.

        Args:
            email: The user's email.
            password_hash: The pre-hashed password.

        Returns:
            A dictionary containing the created user's details.
        """
        email = email.strip().lower()
        user_id = email
        is_admin = (email == self.admin_email)
        user_data = {
            "id": user_id,
            "email": email,
            "password_hash": password_hash,
            "is_admin": is_admin,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        if self.is_deployed:
            doc_ref = self.firestore_client.collection(self.collection_name).document(email)
            await doc_ref.set(user_data)
            return user_data
        else:
            def _create():
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO users (id, email, password_hash, is_admin) VALUES (?, ?, ?, ?)",
                        (user_id, email, password_hash, 1 if is_admin else 0)
                    )
                    conn.commit()
                    return user_data
                finally:
                    conn.close()
            import asyncio
            return await asyncio.to_thread(_create)

    async def list_users(self) -> list[Dict[str, Any]]:
        """Lists all registered users.

        Returns:
            A list of user dictionaries.
        """
        if self.is_deployed:
            users_ref = self.firestore_client.collection(self.collection_name)
            docs = users_ref.stream()
            users_list = []
            async for doc in docs:
                data = doc.to_dict() or {}
                # Strip password hash before returning
                data.pop("password_hash", None)
                users_list.append(data)
            # Sort by created_at
            users_list.sort(key=lambda u: u.get("created_at", ""))
            return users_list
        else:
            def _list():
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id, email, is_admin, created_at FROM users")
                    rows = cursor.fetchall()
                    users_list = []
                    for row in rows:
                        users_list.append({
                            "id": row[0],
                            "email": row[1],
                            "is_admin": bool(row[2]),
                            "created_at": row[3]
                        })
                    return users_list
                finally:
                    conn.close()
            import asyncio
            users = await asyncio.to_thread(_list)
            users.sort(key=lambda u: u.get("created_at", ""))
            return users

    async def toggle_admin(self, email: str) -> bool:
        """Toggles administrative privileges for a user.

        Args:
            email: The user's email.

        Returns:
            The new is_admin boolean state.
        """
        email = email.strip().lower()
        if email == self.admin_email:
            # The primary admin cannot be demoted
            return True

        user = await self.get_user(email)
        if not user:
            raise ValueError("User not found")

        new_state = not user.get("is_admin", False)

        if self.is_deployed:
            doc_ref = self.firestore_client.collection(self.collection_name).document(email)
            await doc_ref.update({"is_admin": new_state})
            return new_state
        else:
            def _toggle():
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE users SET is_admin = ? WHERE email = ?", (1 if new_state else 0, email))
                    conn.commit()
                    return new_state
                finally:
                    conn.close()
            import asyncio
            return await asyncio.to_thread(_toggle)
