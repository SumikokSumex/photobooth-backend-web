from pathlib import Path
from datetime import datetime
import json
import shutil
import traceback
import time

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from config import ALLOWED_ORIGINS, GOOGLE_DRIVE_PARENT_FOLDER_ID
from services.google_drive_service import GoogleDriveService
from services.nanobanana_service import NanoBananaService

app = FastAPI(title="Photobooth Backend Web")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

PROMPT_CATALOG_FILE = Path("data/prompt_catalog.json")


def get_session_dir(session_id: str) -> Path:
    return UPLOAD_DIR / session_id


def get_session_info_path(session_id: str) -> Path:
    return get_session_dir(session_id) / "session_info.json"


def load_session_info(session_id: str) -> dict:
    session_info_path = get_session_info_path(session_id)
    if not session_info_path.exists():
        raise FileNotFoundError(
            f"session_info.json tidak ditemukan untuk session {session_id}"
        )

    with session_info_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_session_info(session_id: str, data: dict) -> None:
    session_dir = get_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    session_info_path = get_session_info_path(session_id)
    with session_info_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def find_session_id_by_task_id(task_id: str) -> str | None:
    for info_file in UPLOAD_DIR.glob("*/session_info.json"):
        try:
            with info_file.open("r", encoding="utf-8") as file:
                data = json.load(file)

            if data.get("nanobanana_task_id") == task_id:
                return info_file.parent.name
        except Exception:
            continue

    return None


def finalize_ai_result_for_session(session_id: str, result_image_url: str) -> None:
    session_info = load_session_info(session_id)

    if session_info.get("drive_ai_result_file_id"):
        print(
            f"[SESSION {session_id}] Final AI sudah pernah diupload, skip duplicate process"
        )
        return

    session_dir = get_session_dir(session_id)
    ai_result_path = session_dir / "final_result.jpg"

    print(f"[SESSION {session_id}] NanoBanana success. result_image_url={result_image_url}")
    print(f"[SESSION {session_id}] Mulai download hasil AI ke {ai_result_path}")

    nanobanana_service = NanoBananaService()
    nanobanana_service.download_result_image(
        image_url=result_image_url,
        output_path=str(ai_result_path),
    )

    drive_service = GoogleDriveService(
        parent_folder_id=GOOGLE_DRIVE_PARENT_FOLDER_ID
    )

    drive_folder_id = session_info.get("drive_folder_id")
    if not drive_folder_id:
        raise Exception(f"drive_folder_id tidak ditemukan untuk session {session_id}")

    print(f"[SESSION {session_id}] Mulai upload final_result.jpg ke Google Drive")
    print(f"[SESSION {session_id}] drive_folder_id={drive_folder_id}")

    uploaded_ai_file = drive_service.upload_file_to_folder(
        file_path=str(ai_result_path),
        filename="final_result.jpg",
        folder_id=drive_folder_id,
    )

    print(
        f"[SESSION {session_id}] Upload final_result.jpg berhasil. "
        f"file_id={uploaded_ai_file['id']}"
    )

    session_info["nanobanana_status"] = "success"
    session_info["nanobanana_error_message"] = None
    session_info["nanobanana_result_image_url"] = result_image_url
    session_info["drive_ai_result_file_id"] = uploaded_ai_file["id"]
    session_info["drive_ai_result_file_url"] = uploaded_ai_file.get("webViewLink")

    save_session_info(session_id, session_info)

def poll_nanobanana_until_done(session_id: str, task_id: str) -> None:
    print(f"[SESSION {session_id}] Background polling dimulai untuk task {task_id}")

    nanobanana_service = NanoBananaService()

    max_attempts = 90
    sleep_seconds = 8

    for attempt in range(1, max_attempts + 1):
        try:
            task_result = nanobanana_service.get_task_details(task_id)

            print(f"=== BACKGROUND POLLING TASK RESULT | SESSION {session_id} | ATTEMPT {attempt} ===")
            print(json.dumps(task_result, ensure_ascii=False, indent=2))

            task_data = task_result.get("data", {}) or {}
            success_flag = task_data.get("successFlag")
            response_data = task_data.get("response") or {}
            result_image_url = response_data.get("resultImageUrl")
            error_code = task_data.get("errorCode")
            error_message = task_data.get("errorMessage") or task_result.get("msg")

            session_info = load_session_info(session_id)

            if success_flag == 1 and result_image_url:
                finalize_ai_result_for_session(session_id, result_image_url)
                print(f"[SESSION {session_id}] Background polling selesai: success")
                return

            if success_flag in (2, 3):
                session_info["nanobanana_status"] = "failed"
                session_info["nanobanana_error_message"] = (
                    error_message or f"NanoBanana gagal dengan errorCode={error_code}"
                )
                save_session_info(session_id, session_info)
                print(
                    f"[SESSION {session_id}] Background polling selesai: failed - "
                    f"{session_info['nanobanana_error_message']}"
                )
                return

            session_info["nanobanana_status"] = "queued"
            session_info["nanobanana_error_message"] = None
            save_session_info(session_id, session_info)

        except Exception as e:
            traceback.print_exc()
            try:
                session_info = load_session_info(session_id)
                session_info["nanobanana_status"] = "failed"
                session_info["nanobanana_error_message"] = f"{type(e).__name__}: {str(e)}"
                save_session_info(session_id, session_info)
            except Exception:
                pass

            print(f"[SESSION {session_id}] Background polling error: {type(e).__name__}: {str(e)}")
            return

        time.sleep(sleep_seconds)

    try:
        session_info = load_session_info(session_id)
        session_info["nanobanana_status"] = "failed"
        session_info["nanobanana_error_message"] = (
            f"Timeout: task NanoBanana tidak selesai setelah {max_attempts * sleep_seconds} detik"
        )
        save_session_info(session_id, session_info)
    except Exception:
        pass

    print(f"[SESSION {session_id}] Background polling timeout")
    
def process_nanobanana_callback(payload: dict) -> None:
    try:
        print("=== NANO BANANA CALLBACK PAYLOAD ===")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        code = payload.get("code")
        msg = payload.get("msg")
        data = payload.get("data", {}) or {}

        task_id = data.get("taskId")
        response_data = data.get("response") or {}
        info_data = data.get("info") or {}

        result_image_url = (
            response_data.get("resultImageUrl")
            or info_data.get("resultImageUrl")
        )

        error_message = data.get("errorMessage") or msg
        success_flag = data.get("successFlag")
        error_code = data.get("errorCode")

        if not task_id:
            print("Callback NanoBanana tidak mengandung taskId")
            return

        session_id = find_session_id_by_task_id(task_id)
        if not session_id:
            print(f"Tidak menemukan session untuk taskId {task_id}")
            return

        session_info = load_session_info(session_id)

        if code != 200 or success_flag in (2, 3):
            session_info["nanobanana_status"] = "failed"
            session_info["nanobanana_error_message"] = (
                error_message or f"NanoBanana gagal dengan errorCode={error_code}"
            )
            save_session_info(session_id, session_info)
            print(
                f"[SESSION {session_id}] NanoBanana gagal: "
                f"{session_info['nanobanana_error_message']}"
            )
            return

        if success_flag != 1:
            session_info["nanobanana_status"] = "queued"
            session_info["nanobanana_error_message"] = None
            save_session_info(session_id, session_info)
            print(f"[SESSION {session_id}] Callback datang tetapi task belum final")
            return

        if not result_image_url:
            session_info["nanobanana_status"] = "failed"
            session_info["nanobanana_error_message"] = "resultImageUrl kosong"
            save_session_info(session_id, session_info)
            print(f"[SESSION {session_id}] Callback sukses tapi resultImageUrl kosong")
            return

        finalize_ai_result_for_session(session_id, result_image_url)

    except Exception as e:
        traceback.print_exc()
        try:
            data = payload.get("data", {}) or {}
            task_id = data.get("taskId")
            if task_id:
                session_id = find_session_id_by_task_id(task_id)
                if session_id:
                    session_info = load_session_info(session_id)
                    session_info["nanobanana_status"] = "failed"
                    session_info["nanobanana_error_message"] = (
                        f"{type(e).__name__}: {str(e)}"
                    )
                    save_session_info(session_id, session_info)
        except Exception:
            pass


@app.get("/")
def read_root():
    return {"message": "Backend photobooth web berjalan"}


@app.get("/health")
def health_check():
    return {
        "success": True,
        "service": "photobooth-backend-web",
        "status": "ok",
    }


@app.get("/prompt-catalog")
def get_prompt_catalog():
    try:
        if not PROMPT_CATALOG_FILE.exists():
            raise HTTPException(
                status_code=404,
                detail=f"prompt_catalog.json tidak ditemukan di {PROMPT_CATALOG_FILE}",
            )

        with PROMPT_CATALOG_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        return {
            "success": True,
            "items": data,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Gagal membaca prompt catalog: {type(e).__name__}: {str(e)}",
        )


@app.post("/upload-photo")
async def upload_photo(
    background_tasks: BackgroundTasks,
    photo: UploadFile = File(...),
    prompt_title: str = Form(...),
    prompt_text: str = Form(...),
    aspect_ratio: str = Form(...),
):
    session_id = None

    try:
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        session_dir = UPLOAD_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        extension = Path(photo.filename or "").suffix.lower() or ".jpg"
        original_filename = f"original{extension}"
        original_path = session_dir / original_filename

        with original_path.open("wb") as buffer:
            shutil.copyfileobj(photo.file, buffer)

        drive_service = GoogleDriveService(
            parent_folder_id=GOOGLE_DRIVE_PARENT_FOLDER_ID
        )

        drive_folder = drive_service.create_session_folder(session_id=session_id)

        drive_original_file = drive_service.upload_file_to_folder(
            file_path=str(original_path),
            filename=original_filename,
            folder_id=drive_folder["id"],
        )

        drive_original_public_image_url = drive_service.build_public_image_url(
            file_id=drive_original_file["id"]
        )

        nanobanana_service = NanoBananaService()
        nanobanana_task = nanobanana_service.submit_image_edit_task(
            prompt=prompt_text,
            image_url=drive_original_public_image_url,
            aspect_ratio=aspect_ratio,
        )

        session_info = {
            "success": True,
            "session_id": session_id,
            "prompt_title": prompt_title,
            "prompt_text": prompt_text,
            "aspect_ratio": aspect_ratio,
            "created_at": datetime.now().isoformat(),
            "original_filename": original_filename,
            "original_path": str(original_path.resolve()),
            "nanobanana_task_id": nanobanana_task["task_id"],
            "nanobanana_status": "queued",
            "nanobanana_error_message": None,
            "nanobanana_result_image_url": None,
            "drive_folder_id": drive_folder["id"],
            "drive_folder_url": drive_folder.get("webViewLink"),
            "drive_original_file_id": drive_original_file["id"],
            "drive_original_file_url": drive_original_file.get("webViewLink"),
            "drive_original_public_image_url": drive_original_public_image_url,
            "drive_ai_result_file_id": None,
            "drive_ai_result_file_url": None,
        }

        save_session_info(session_id, session_info)

        background_tasks.add_task(
            poll_nanobanana_until_done,
            session_id,
            nanobanana_task["task_id"],
        )

        return {
            "success": True,
            "message": "Foto berhasil diupload, original dikirim ke Drive, dan task NanoBanana dibuat",
            "session_id": session_id,
            "nanobanana_task_id": nanobanana_task["task_id"],
            "nanobanana_status": "queued",
            "drive_folder_url": drive_folder.get("webViewLink"),
            "drive_original_public_image_url": drive_original_public_image_url,
        }

    except Exception as e:
        error_message = f"{type(e).__name__}: {str(e)}"
        traceback.print_exc()

        if session_id:
            failed_info = {
                "success": False,
                "session_id": session_id,
                "prompt_title": prompt_title,
                "created_at": datetime.now().isoformat(),
                "error": error_message,
            }
            save_session_info(session_id, failed_info)

        raise HTTPException(
            status_code=500,
            detail=f"Gagal proses upload-photo: {error_message}",
        )


@app.get("/session/{session_id}")
def get_session_status(session_id: str):
    try:
        session_info = load_session_info(session_id)
        task_id = session_info.get("nanobanana_task_id")

        if not task_id:
            return {
                "success": True,
                "session_id": session_id,
                "data": session_info,
            }

        if session_info.get("drive_ai_result_file_id"):
            session_info["nanobanana_status"] = "success"
            save_session_info(session_id, session_info)
            return {
                "success": True,
                "session_id": session_id,
                "data": session_info,
            }

        nanobanana_service = NanoBananaService()
        task_result = nanobanana_service.get_task_details(task_id)

        print("=== NANO BANANA TASK RESULT ===")
        print(json.dumps(task_result, ensure_ascii=False, indent=2))

        task_data = task_result.get("data", {}) or {}
        success_flag = task_data.get("successFlag")
        response_data = task_data.get("response") or {}
        result_image_url = response_data.get("resultImageUrl")
        error_code = task_data.get("errorCode")
        error_message = task_data.get("errorMessage") or task_result.get("msg")

        if success_flag == 1 and result_image_url:
            try:
                finalize_ai_result_for_session(session_id, result_image_url)
                session_info = load_session_info(session_id)
            except Exception as finalize_error:
                traceback.print_exc()
                session_info["nanobanana_status"] = "failed"
                session_info["nanobanana_error_message"] = (
                    f"{type(finalize_error).__name__}: {str(finalize_error)}"
                )
                save_session_info(session_id, session_info)

        elif success_flag in (2, 3):
            session_info["nanobanana_status"] = "failed"
            session_info["nanobanana_error_message"] = (
                error_message or f"NanoBanana gagal dengan errorCode={error_code}"
            )
            save_session_info(session_id, session_info)

        else:
            session_info["nanobanana_status"] = "queued"
            session_info["nanobanana_error_message"] = None
            save_session_info(session_id, session_info)

        return {
            "success": True,
            "session_id": session_id,
            "data": session_info,
        }

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id} tidak ditemukan",
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Gagal membaca session {session_id}: {type(e).__name__}: {str(e)}",
        )


@app.post("/nanobanana/callback")
async def nanobanana_callback(payload: dict, background_tasks: BackgroundTasks):
    background_tasks.add_task(process_nanobanana_callback, payload)
    return {"status": "received"}
