import json
import numpy as np
from deepface import DeepFace
from typing import List, Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


def detect_faces_in_image(image_path: str) -> List[Dict]:
    """
    Detect faces in an image and return face data with embeddings and bounding boxes.

    Args:
        image_path: Path to the image file

    Returns:
        List of dictionaries containing face data:
        - embedding: Face embedding vector (list of floats)
        - bbox: Bounding box dict {x, y, w, h}
        - confidence: Detection confidence score
    """
    try:
        # Use DeepFace to detect and extract faces
        # detector_backend options: opencv, ssd, dlib, mtcnn, retinaface, mediapipe
        # model options: VGG-Face, Facenet, OpenFace, DeepFace, DeepID, ArcFace, Dlib, SFace

        # Detect faces with RetinaFace (good balance of speed and accuracy)
        face_objs = DeepFace.extract_faces(
            img_path=image_path,
            detector_backend='retinaface',
            enforce_detection=False,  # Don't fail if no faces found
            align=True
        )

        if not face_objs:
            return []

        results = []

        for face_obj in face_objs:
            # Get face region info
            facial_area = face_obj.get('facial_area', {})
            confidence = face_obj.get('confidence', 0.0)

            # Skip low confidence detections
            if confidence < 0.5:
                continue

            # Extract embedding for this face
            # We'll use Facenet512 for high-quality embeddings (512D)
            embedding_obj = DeepFace.represent(
                img_path=image_path,
                model_name='Facenet512',
                detector_backend='skip',  # Skip detection since we already detected
                enforce_detection=False
            )

            # DeepFace.represent returns a list, get first element
            if embedding_obj and len(embedding_obj) > 0:
                embedding = embedding_obj[0]['embedding']
            else:
                continue

            results.append({
                'embedding': embedding,
                'bbox': {
                    'x': facial_area.get('x', 0),
                    'y': facial_area.get('y', 0),
                    'w': facial_area.get('w', 0),
                    'h': facial_area.get('h', 0)
                },
                'confidence': float(confidence)
            })

        return results

    except Exception as e:
        logger.error(f"Error detecting faces in {image_path}: {str(e)}")
        return []


def calculate_embedding_similarity(embedding1: List[float], embedding2: List[float]) -> float:
    """
    Calculate cosine similarity between two face embeddings.

    Args:
        embedding1: First face embedding
        embedding2: Second face embedding

    Returns:
        Similarity score between 0 and 1 (1 = identical)
    """
    try:
        vec1 = np.array(embedding1)
        vec2 = np.array(embedding2)

        # Cosine similarity
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        similarity = dot_product / (norm1 * norm2)
        return float(similarity)

    except Exception as e:
        logger.error(f"Error calculating similarity: {str(e)}")
        return 0.0


def find_matching_person(
    new_embedding: List[float],
    existing_persons: List[Tuple[int, List[float]]],
    threshold: float = 0.6
) -> Optional[int]:
    """
    Find if a new face embedding matches any existing person.

    Args:
        new_embedding: Embedding of the new face
        existing_persons: List of (person_id, representative_embedding) tuples
        threshold: Minimum similarity score to consider a match (default 0.6)

    Returns:
        person_id of matching person, or None if no match found
    """
    best_match_id = None
    best_similarity = threshold

    for person_id, person_embedding in existing_persons:
        similarity = calculate_embedding_similarity(new_embedding, person_embedding)

        if similarity > best_similarity:
            best_similarity = similarity
            best_match_id = person_id

    return best_match_id


def cluster_faces_dbscan(embeddings: List[List[float]], eps: float = 0.5, min_samples: int = 1) -> List[int]:
    """
    Cluster face embeddings using DBSCAN algorithm.

    Args:
        embeddings: List of face embedding vectors
        eps: Maximum distance between two samples for one to be considered as in the neighborhood of the other
        min_samples: Minimum number of samples in a neighborhood for a point to be considered a core point

    Returns:
        List of cluster labels (same length as embeddings)
    """
    try:
        from sklearn.cluster import DBSCAN

        if len(embeddings) == 0:
            return []

        # Convert to distance matrix using cosine distance
        X = np.array(embeddings)

        # DBSCAN clustering
        clustering = DBSCAN(eps=eps, min_samples=min_samples, metric='cosine')
        labels = clustering.fit_predict(X)

        return labels.tolist()

    except Exception as e:
        logger.error(f"Error clustering faces: {str(e)}")
        return list(range(len(embeddings)))  # Return unique label for each face
