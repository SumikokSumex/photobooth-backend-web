from pathlib import Path
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = ["https://www.googleapis.com/auth/drive"]


class GoogleDriveService:
    def __init__(self, parent_folder_id: str):
        self.parent_folder_id = parent_folder_id     
        self.oauth_client_file = Path(
            os.getenv("GOOGLE_OAUTH_CLIENT_FILE", "/etc/secrets/oauth_client.json")
        )
        self.token_file = Path(
            os.getenv("GOOGLE_TOKEN_FILE", "/etc/secrets/token.json")
        )
        self.service = build("drive", "v3", credentials=self._get_credentials())

    def _get_credentials(self):
        creds = None

        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(
                str(self.token_file),
                SCOPES,
            )

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

            try:
                with self.token_file.open("w", encoding="utf-8") as token:
                    token.write(creds.to_json())
            except Exception:
                # Di hosting tertentu file secret / filesystem bisa tidak persisten atau tidak writable.
                # Tidak masalah selama refresh token berhasil dan creds sudah valid di memory.
                pass

            return creds

        if creds and creds.valid:
            return creds

        if not self.oauth_client_file.exists():
            raise FileNotFoundError(
                f"File oauth client tidak ditemukan di {self.oauth_client_file}"
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            str(self.oauth_client_file),
            SCOPES,
        )

        creds = flow.run_local_server(port=0)

        self.credentials_dir.mkdir(parents=True, exist_ok=True)
        with self.token_file.open("w", encoding="utf-8") as token:
            token.write(creds.to_json())

        return creds

    def create_session_folder(self, session_id: str):
        folder_metadata = {
            "name": session_id,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [self.parent_folder_id],
        }

        folder = (
            self.service.files()
            .create(body=folder_metadata, fields="id, name, webViewLink")
            .execute()
        )

        folder_id = folder["id"]

        self.service.permissions().create(
            fileId=folder_id,
            body={
                "type": "anyone",
                "role": "reader",
            },
        ).execute()

        folder_info = (
            self.service.files()
            .get(fileId=folder_id, fields="id, name, webViewLink")
            .execute()
        )

        return folder_info

    def upload_file_to_folder(self, file_path: str, filename: str, folder_id: str):
        media = MediaFileUpload(file_path, resumable=False)

        uploaded_file = (
            self.service.files()
            .create(
                body={
                    "name": filename,
                    "parents": [folder_id],
                },
                media_body=media,
                fields="id, name, webViewLink",
            )
            .execute()
        )

        return uploaded_file

    def build_public_image_url(self, file_id: str):
        self.service.permissions().create(
            fileId=file_id,
            body={
                "type": "anyone",
                "role": "reader",
            },
        ).execute()

        return f"https://drive.google.com/uc?export=download&id={file_id}"
