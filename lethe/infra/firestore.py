from google.cloud import firestore

from lethe.config import Config


def create_firestore_client(config: Config) -> firestore.AsyncClient:
    return firestore.AsyncClient(project=config.google_cloud_project)
