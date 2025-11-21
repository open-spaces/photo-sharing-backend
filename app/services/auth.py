from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.core.config import config
from app.models.models import Token, GoogleToken
from app.db.deps import get_db
from app.db import models as orm
from sqlalchemy.orm import Session

security = HTTPBearer()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, config.SECRET_KEY, algorithm=config.ALGORITHM)
    return encoded_jwt

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(credentials.credentials, config.SECRET_KEY, algorithms=[config.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        # Ensure user exists in DB
        user = db.query(orm.User).filter(orm.User.username == username).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        # Optional: verify session token exists and is not expired/revoked
        session = (
            db.query(orm.Session)
            .filter(orm.Session.user_id == user.id, orm.Session.token == credentials.credentials)
            .first()
        )
        if not session or session.revoked or session.expires_at <= datetime.utcnow():
            raise HTTPException(status_code=401, detail="Session expired or invalid")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

class AuthService:
    @staticmethod
    async def google_login(google_token: GoogleToken, db: Session) -> Token:
        try:
            # Verify the Google token
            idinfo = id_token.verify_oauth2_token(
                google_token.token, google_requests.Request(), config.GOOGLE_CLIENT_ID
            )
            
            # Check if the token is valid
            if idinfo['iss'] not in ['accounts.google.com', 'https://accounts.google.com']:
                raise HTTPException(status_code=400, detail="Wrong issuer")
            
            # Extract user info from Google token
            google_id = idinfo['sub']
            email = idinfo['email']
            name = idinfo.get('name', email)
            
            # Create or get existing user
            username = f"google_{google_id}"

            # Create or fetch user in DB
            user = db.query(orm.User).filter(orm.User.username == username).first()
            if not user:
                user = orm.User(
                    username=username,
                    email=email,
                    name=name,
                    google_id=google_id,
                    provider="google",
                )
                db.add(user)
                db.commit()
                db.refresh(user)

            # Create access token
            access_token_expires = timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)
            access_token = create_access_token(
                data={"sub": username}, expires_delta=access_token_expires
            )
            # Persist session
            session = orm.Session(
                user_id=user.id,
                token=access_token,
                expires_at=datetime.utcnow() + access_token_expires,
            )
            db.add(session)
            db.commit()

            return Token(
                access_token=access_token,
                token_type="bearer",
                username=name  # Use the display name instead of internal username
            )
        
        except ValueError as e:
            # Invalid token
            raise HTTPException(status_code=400, detail="Invalid Google token")
        except Exception as e:
            raise HTTPException(status_code=500, detail="Authentication failed")

