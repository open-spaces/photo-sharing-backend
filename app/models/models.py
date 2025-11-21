from pydantic import BaseModel
from typing import Optional, Dict, List

class Token(BaseModel):
    access_token: str
    token_type: str
    username: str

class GoogleToken(BaseModel):
    token: str

class User(BaseModel):
    username: str
    email: str
    name: str
    provider: str
    google_id: Optional[str] = None


class PhotoOut(BaseModel):
    id: int
    url: str
    original_filename: str
    stored_filename: str
    content_type: Optional[str]
    size_bytes: int
    width: Optional[int]
    height: Optional[int]
    sha256: Optional[str]
    uploaded_at: str
    uploader_name: Optional[str]


class FaceOut(BaseModel):
    id: int
    photo_id: int
    person_id: Optional[int]
    bbox: Dict[str, int]
    confidence: Optional[float]
    photo_url: str


class PersonOut(BaseModel):
    id: int
    name: Optional[str]
    face_count: int
    representative_face: Optional[FaceOut]


class PersonWithPhotosOut(BaseModel):
    id: int
    name: Optional[str]
    face_count: int
    photos: List[PhotoOut]
