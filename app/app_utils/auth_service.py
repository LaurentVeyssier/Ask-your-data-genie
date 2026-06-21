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

        if not self.is_deployed:
            self._init_sqlite()
        else:
            self.firestore_client = AsyncClient()

    def _init_sqlite(self) -> None:
        """Creates the sqlite users table if it does not exist."""
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
                return doc.to_dict()
            return None
        else:
            def _get():
                conn = sqlite3.connect(self.db_path)
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (email,))
                    row = cursor.fetchone()
                    if row:
                        return {"id": row[0], "email": row[1], "password_hash": row[2]}
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
        user_data = {
            "id": user_id,
            "email": email,
            "password_hash": password_hash,
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
                        "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
                        (user_id, email, password_hash)
                    )
                    conn.commit()
                    return user_data
                finally:
                    conn.close()
            import asyncio
            return await asyncio.to_thread(_create)
