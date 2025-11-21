from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, Depends, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import shutil
from typing import List
from PIL import Image
import hashlib

from app.core.config import config
from app.models.models import Token, GoogleToken, PhotoOut, PersonOut, PersonWithPhotosOut, FaceOut
from app.services.auth import AuthService, verify_token
from app.services.websocket_manager import websocket_manager
from app.services.face_service import detect_faces_in_image, find_matching_person
from app.core.utils import extract_image_metadata, get_safe_filename, is_allowed_file_type, is_file_size_valid, validate_image_content
from app.db.database import init_db
from app.db.deps import get_db
from sqlalchemy.orm import Session
from app.db import models as orm
from fastapi import Path, Response
import time
import logging
import json

# Create upload directory
os.makedirs(config.UPLOAD_DIR, exist_ok=True)

app = FastAPI(
    title="Image Upload API",
    description="API for uploading image files with OAuth authentication",
    version="2.0.0"
)

logger = logging.getLogger("photos")
logging.basicConfig(level=logging.INFO)

# lightweight in-memory cache to dampen rapid repeat calls
_CACHE_TTL_SECONDS = 5
_photos_cache: dict[str, tuple[float, list[PhotoOut]]] = {}

# Configure CORS - restrict origins in production
allowed_origins = ["*"]  # For development - should be restricted in production

# Check if running in production (not localhost/127.0.0.1)
is_production = (
    "localhost" not in config.PUBLIC_URL.lower() and
    "127.0.0.1" not in config.PUBLIC_URL
)

if is_production:
    # In production, specify actual frontend domains
    allowed_origins = [
        "https://wedding.open-spaces.xyz",
        "https://www.wedding.open-spaces.xyz",
        "http://wedding.open-spaces.xyz",
        "http://www.wedding.open-spaces.xyz"
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"]
)

# Ensure DB tables exist on startup (works for uvicorn run too)
@app.on_event("startup")
async def _startup():
    init_db()

# Mount static files
app.mount("/api/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")

# Background task for face detection
def process_face_detection(photo_id: int, image_path: str):
    """
    Background task to detect faces in an uploaded image.
    This runs asynchronously after the upload completes.
    """
    from app.db.database import SessionLocal
    db = SessionLocal()

    try:
        logger.info(f"Starting face detection for photo_id={photo_id}")

        # Detect faces in the uploaded image
        face_data_list = detect_faces_in_image(image_path)

        for face_data in face_data_list:
            embedding = face_data['embedding']
            bbox = face_data['bbox']
            confidence = face_data['confidence']

            # Try to match this face with existing persons
            existing_persons_query = db.query(
                orm.Person.id,
                orm.Face.embedding
            ).join(orm.Face, orm.Person.id == orm.Face.person_id).all()

            existing_persons = [
                (person_id, json.loads(embedding_json))
                for person_id, embedding_json in existing_persons_query
            ]

            # Find matching person (or None if no match)
            matched_person_id = find_matching_person(
                embedding,
                existing_persons,
                threshold=0.6
            )

            # If no match, create a new person
            if matched_person_id is None:
                new_person = orm.Person()
                db.add(new_person)
                db.commit()
                db.refresh(new_person)
                matched_person_id = new_person.id

            # Create face record
            face = orm.Face(
                photo_id=photo_id,
                person_id=matched_person_id,
                embedding=json.dumps(embedding),
                bbox_json=json.dumps(bbox),
                confidence=confidence
            )
            db.add(face)

        db.commit()
        logger.info(f"Face detection completed for photo_id={photo_id}, found {len(face_data_list)} faces")

    except Exception as e:
        logger.error(f"Error detecting faces for photo_id={photo_id}: {str(e)}")
        db.rollback()
    finally:
        db.close()

# Authentication endpoints
@app.post("/google-login", response_model=Token)
async def google_login(google_token: GoogleToken, db: Session = Depends(get_db)):
    return await AuthService.google_login(google_token, db)


@app.get("/verify-token")
async def verify_user_token(current_user: str = Depends(verify_token)):
    return {"username": current_user, "valid": True}

# File upload endpoints
@app.post(
    "/upload",
    summary="Upload image files",
    description="Upload up to 10 image files (jpg, jpeg, png)."
)
async def upload_images(
    background_tasks: BackgroundTasks,
    images: list[UploadFile] = File(..., description="List of image files"),
    current_user: str = Depends(verify_token),
    db: Session = Depends(get_db),
):
    added = 0
    duplicates = 0
    duplicate_filenames = []
    for image in images:
        # Validate file type
        if not is_allowed_file_type(image.filename):
            raise HTTPException(status_code=400, detail="Only JPG, JPEG, and PNG files are allowed.")

        # Validate file size
        image.file.seek(0, os.SEEK_END)
        size = image.file.tell()
        image.file.seek(0)
        
        if not is_file_size_valid(size, config.MAX_FILE_SIZE):
            raise HTTPException(status_code=413, detail="File too large. Max size is 5MB.")

        # Generate unique filename to prevent conflicts and improve security
        import uuid
        file_extension = os.path.splitext(get_safe_filename(image.filename))[1].lower()
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        save_path = os.path.join(config.UPLOAD_DIR, unique_filename)

        # Save file while computing SHA-256 hash
        hasher = hashlib.sha256()
        with open(save_path, "wb") as buffer:
            while True:
                chunk = image.file.read(8192)
                if not chunk:
                    break
                buffer.write(chunk)
                hasher.update(chunk)
        image_hash = hasher.hexdigest()
        
        # Validate that the uploaded file is actually an image
        if not validate_image_content(save_path):
            os.remove(save_path)  # Remove invalid file
            raise HTTPException(status_code=400, detail=f"Invalid image file: {image.filename}")

        # Duplicate check by sha256
        existing = db.query(orm.Photo).filter(orm.Photo.sha256 == image_hash).first()
        if existing:
            try:
                os.remove(save_path)
            except Exception:
                pass
            duplicates += 1
            duplicate_filenames.append(image.filename)
            continue

        # Extract metadata and print (or store in database)
        metadata = extract_image_metadata(save_path)
        # Dimensions
        width = height = None
        try:
            with Image.open(save_path) as im:
                width, height = im.size
        except Exception:
            pass

        # Persist photo record
        user = db.query(orm.User).filter(orm.User.username == current_user).first()
        size_bytes = size
        photo = orm.Photo(
            user_id=user.id if user else None,
            original_filename=image.filename,
            stored_filename=unique_filename,
            relative_path=os.path.join(config.UPLOAD_DIR, unique_filename),
            content_type=getattr(image, "content_type", None),
            size_bytes=size_bytes,
            metadata_json=__import__("json").dumps(metadata),
            width=width,
            height=height,
            sha256=image_hash,
        )
        db.add(photo)
        db.commit()
        db.refresh(photo)  # Get the photo ID

        # Schedule face detection to run in the background
        background_tasks.add_task(process_face_detection, photo.id, save_path)
        logger.info(f"Scheduled face detection for photo_id={photo.id}")

        added += 1

    return JSONResponse(content={
        "message": "Files uploaded successfully",
        "count": added,
        "duplicates": duplicates,
        "duplicate_filenames": duplicate_filenames
    })

# NOTE: The legacy /get-images endpoint has been removed.
# Use /photos for the canonical image listing.


@app.get("/photos", response_model=list[PhotoOut])
async def list_photos(request: Request, db: Session = Depends(get_db), response: Response = None):
    now = time.time()
    cache_key = "all"
    cached = _photos_cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        if response is not None:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return cached[1]
    base = str(request.base_url).rstrip("/")
    rows = (
        db.query(orm.Photo, orm.User)
        .outerjoin(orm.User, orm.Photo.user_id == orm.User.id)
        .order_by(orm.Photo.uploaded_at.desc())
        .all()
    )
    result: list[PhotoOut] = []
    for photo, user in rows:
        url = f"{base}/uploads/{photo.stored_filename}"
        result.append(
            PhotoOut(
                id=photo.id,
                url=url,
                original_filename=photo.original_filename,
                stored_filename=photo.stored_filename,
                content_type=photo.content_type,
                size_bytes=photo.size_bytes,
                width=photo.width,
                height=photo.height,
                sha256=photo.sha256,
                uploaded_at=photo.uploaded_at.isoformat(),
                uploader_name=(user.name if user else None),
            )
        )
    _photos_cache[cache_key] = (time.time(), result)
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return result

@app.get("/my-photos", response_model=list[PhotoOut], summary="List photos uploaded by current user")
async def my_photos(request: Request, db: Session = Depends(get_db), current_user: str = Depends(verify_token), response: Response = None):
    logger.info("/my-photos requested by=%s ua=%s ip=%s", current_user, request.headers.get("user-agent"), request.client.host if request.client else "?")

    now = time.time()
    cache_key = f"user:{current_user}"
    cached = _photos_cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        if response is not None:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return cached[1]
    base = str(request.base_url).rstrip("/")
    user = db.query(orm.User).filter(orm.User.username == current_user).first()
    if not user:
        return []
    rows = (
        db.query(orm.Photo)
        .filter(orm.Photo.user_id == user.id)
        .order_by(orm.Photo.uploaded_at.desc())
        .all()
    )
    result: list[PhotoOut] = []
    for photo in rows:
        url = f"{base}/uploads/{photo.stored_filename}"
        result.append(
            PhotoOut(
                id=photo.id,
                url=url,
                original_filename=photo.original_filename,
                stored_filename=photo.stored_filename,
                content_type=photo.content_type,
                size_bytes=photo.size_bytes,
                width=photo.width,
                height=photo.height,
                sha256=photo.sha256,
                uploaded_at=photo.uploaded_at.isoformat(),
                uploader_name=None,
            )
        )
    _photos_cache[cache_key] = (time.time(), result)
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return result

@app.delete("/photos/{photo_id}", summary="Delete a photo uploaded by the current user")
async def delete_photo(
    photo_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    current_user: str = Depends(verify_token),
):
    user = db.query(orm.User).filter(orm.User.username == current_user).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    photo = db.query(orm.Photo).filter(orm.Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    if photo.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not allowed to delete this photo")
    # Remove file from storage
    try:
        file_path = os.path.join(config.UPLOAD_DIR, photo.stored_filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        # Continue even if file is missing; DB will be authoritative
        pass
    # Remove DB record
    db.delete(photo)
    db.commit()
    return {"status": "deleted", "id": photo_id}


# Face recognition endpoints
@app.post("/process-existing-photos", summary="Process all photos without face detection")
async def process_existing_photos(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Process all existing photos that don't have face detection results.
    This is useful for retroactively adding face detection to photos uploaded before the feature was enabled.
    Processes photos in the background and returns immediately.
    """
    # Get all photos that don't have any faces detected
    photos_without_faces = db.query(orm.Photo).outerjoin(
        orm.Face, orm.Photo.id == orm.Face.photo_id
    ).filter(orm.Face.id == None).all()

    # Schedule background tasks for each photo
    for photo in photos_without_faces:
        file_path = os.path.join(config.UPLOAD_DIR, photo.stored_filename)
        if os.path.exists(file_path):
            background_tasks.add_task(process_face_detection, photo.id, file_path)
        else:
            logger.warning(f"Photo file not found: {file_path}")

    return {
        "status": "processing",
        "message": f"Scheduled face detection for {len(photos_without_faces)} photos",
        "photos_scheduled": len(photos_without_faces)
    }


# Legacy synchronous implementation (kept for reference, not used)
def _process_existing_photos_sync(db: Session):
    """
    Synchronous version of process_existing_photos.
    This is kept for reference but not used as an endpoint.
    """
    photos_without_faces = db.query(orm.Photo).outerjoin(
        orm.Face, orm.Photo.id == orm.Face.photo_id
    ).filter(orm.Face.id == None).all()

    processed_count = 0
    faces_detected_count = 0

    for photo in photos_without_faces:
        try:
            file_path = os.path.join(config.UPLOAD_DIR, photo.stored_filename)

            if not os.path.exists(file_path):
                logger.warning(f"Photo file not found: {file_path}")
                continue

            # This is the legacy synchronous implementation
            face_data_list = detect_faces_in_image(file_path)

            for face_data in face_data_list:
                embedding = face_data['embedding']
                bbox = face_data['bbox']
                confidence = face_data['confidence']

                # Try to match with existing persons
                existing_persons_query = db.query(
                    orm.Person.id,
                    orm.Face.embedding
                ).join(orm.Face, orm.Person.id == orm.Face.person_id).all()

                existing_persons = [
                    (person_id, json.loads(embedding_json))
                    for person_id, embedding_json in existing_persons_query
                ]

                # Find matching person
                matched_person_id = find_matching_person(
                    embedding,
                    existing_persons,
                    threshold=0.6
                )

                # Create new person if no match
                if matched_person_id is None:
                    new_person = orm.Person()
                    db.add(new_person)
                    db.commit()
                    db.refresh(new_person)
                    matched_person_id = new_person.id

                # Create face record
                face = orm.Face(
                    photo_id=photo.id,
                    person_id=matched_person_id,
                    embedding=json.dumps(embedding),
                    bbox_json=json.dumps(bbox),
                    confidence=confidence
                )
                db.add(face)
                faces_detected_count += 1

            db.commit()
            processed_count += 1

        except Exception as e:
            logger.error(f"Error processing photo {photo.id}: {str(e)}")
            continue

    # Not used, kept for reference


@app.get("/persons", response_model=list[PersonOut], summary="Get all detected persons with face counts")
async def get_persons(db: Session = Depends(get_db)):
    """
    Get all detected persons grouped by face recognition.
    Returns a list of persons with their face counts and a representative face.
    """
    persons = db.query(orm.Person).all()

    result = []
    for person in persons:
        # Count faces for this person
        face_count = db.query(orm.Face).filter(orm.Face.person_id == person.id).count()

        # Get a representative face (first one)
        representative_face_record = db.query(orm.Face).filter(
            orm.Face.person_id == person.id
        ).first()

        representative_face = None
        if representative_face_record:
            photo = db.query(orm.Photo).filter(
                orm.Photo.id == representative_face_record.photo_id
            ).first()

            if photo:
                representative_face = FaceOut(
                    id=representative_face_record.id,
                    photo_id=representative_face_record.photo_id,
                    person_id=representative_face_record.person_id,
                    bbox=json.loads(representative_face_record.bbox_json),
                    confidence=representative_face_record.confidence,
                    photo_url=f"{config.PUBLIC_URL}/uploads/{photo.stored_filename}"
                )

        result.append(PersonOut(
            id=person.id,
            name=person.name,
            face_count=face_count,
            representative_face=representative_face
        ))

    return result


@app.get("/persons/{person_id}/photos", response_model=list[PhotoOut], summary="Get all photos containing a specific person")
async def get_person_photos(
    person_id: int = Path(..., ge=1),
    db: Session = Depends(get_db)
):
    """
    Get all photos that contain a specific person.
    Returns a list of photos where this person's face was detected.
    """
    # Check if person exists
    person = db.query(orm.Person).filter(orm.Person.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    # Get all photo IDs where this person appears
    photo_ids = db.query(orm.Face.photo_id).filter(
        orm.Face.person_id == person_id
    ).distinct().all()

    photo_ids = [pid[0] for pid in photo_ids]

    if not photo_ids:
        return []

    # Get photo details
    photos = db.query(orm.Photo).filter(orm.Photo.id.in_(photo_ids)).all()

    result = []
    for photo in photos:
        uploader_name = None
        if photo.user_id:
            user = db.query(orm.User).filter(orm.User.id == photo.user_id).first()
            if user:
                uploader_name = user.name

        result.append(PhotoOut(
            id=photo.id,
            url=f"{config.PUBLIC_URL}/uploads/{photo.stored_filename}",
            original_filename=photo.original_filename,
            stored_filename=photo.stored_filename,
            content_type=photo.content_type,
            size_bytes=photo.size_bytes,
            width=photo.width,
            height=photo.height,
            sha256=photo.sha256,
            uploaded_at=photo.uploaded_at.isoformat() if photo.uploaded_at else "",
            uploader_name=uploader_name
        ))

    return result


@app.get("/photos/{photo_id}/faces", response_model=list[FaceOut], summary="Get all faces detected in a specific photo")
async def get_photo_faces(
    photo_id: int = Path(..., ge=1),
    db: Session = Depends(get_db)
):
    """
    Get all faces detected in a specific photo.
    Returns bounding boxes and person IDs for each detected face.
    """
    photo = db.query(orm.Photo).filter(orm.Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    faces = db.query(orm.Face).filter(orm.Face.photo_id == photo_id).all()

    result = []
    for face in faces:
        result.append(FaceOut(
            id=face.id,
            photo_id=face.photo_id,
            person_id=face.person_id,
            bbox=json.loads(face.bbox_json),
            confidence=face.confidence,
            photo_url=f"uploads/{photo.stored_filename}"
        ))

    return result


# WebSocket endpoints
@app.get("/guest")
async def get_guest_count():
    return {"count": websocket_manager.guest_count}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket_manager.handle_websocket(websocket)

if __name__ == "__main__":
    import uvicorn
    # Initialize database tables
    init_db()
    uvicorn.run(app, host=config.SERVER_HOST, port=config.SERVER_PORT)
